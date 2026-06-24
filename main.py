from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import os

from database import get_db, init_db
from models import Client, MonthlyCharge, PaymentTransaction, ClientStatus, ChargeStatus, SystemSetting, PaymentMethod, WaitingList

app = FastAPI(title="Boat Storage Management API")

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)# --- CONFIGURACIÓN DE CORS ---
origins = [
    "http://localhost:3000",
    "https://guarderialachueca.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SlowAPIMiddleware)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ADMIN_SECRET = os.getenv("ADMIN_SECRET")
DEFAULT_MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "100.0"))
DISCOUNT_PERCENTAGE = 0.08

# --- SCHEMAS (Modelos de respuesta) ---
class TransactionResponse(BaseModel):
    id: str
    amount_paid: float
    payment_date: datetime
    method: str

    class Config:
        from_attributes = True

class ChargeResponse(BaseModel):
    id: str
    total_amount: float
    month_period: str
    status: str
    transactions: List[TransactionResponse]

    class Config:
        from_attributes = True

class PrepaymentOption(BaseModel):
    months: int
    total_amount: float
    savings: float

class ClientResponse(BaseModel):
    id: str
    name: str
    phone: str
    box_number: int
    status: str
    is_active: bool
    credit_balance: float
    charges: List[ChargeResponse]
    current_debt: float
    has_discount_current_month: bool
    prepayment_options: List[PrepaymentOption]

    class Config:
        from_attributes = True

# --- ADMIN SCHEMAS ---
class FeeUpdate(BaseModel):
    fee: float

    class Config:
        json_schema_extra = {
            "example": {"fee": 150.0}
        }

class FeeResponse(BaseModel):
    key: str
    value: str

    class Config:
        from_attributes = True

class TransactionCreate(BaseModel):
    charge_id: str
    amount_paid: float
    method: str

    class Config:
        json_schema_extra = {
            "example": {
                "charge_id": "uuid-aqui",
                "amount_paid": 50.0,
                "method": "TRANSFER"
            }
        }

class ClientTransactionCreate(BaseModel):
    amount_paid: float
    method: str

    class Config:
        json_schema_extra = {
            "example": {
                "amount_paid": 100000.0,
                "method": "TRANSFER"
            }
        }

class ChargeCreate(BaseModel):
    client_id: str
    month_period: date
    total_amount: float

    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "uuid-aqui",
                "month_period": "2026-07-01",
                "total_amount": 100.0
            }
        }

class MetricSet(BaseModel):
    invoiced: float
    paid: float
    cash: float
    transfer: float

class TopDebtor(BaseModel):
    name: str
    phone: str
    debt: float

class MonthlyHistory(BaseModel):
    month: str
    revenue: float

class WaitlistCandidate(BaseModel):
    name: str
    phone: str
    box_size: str

class OccupancyStats(BaseModel):
    occupancy_rate: float
    available_boxes: int
    occupied_boxes: int
    total_rentable_boxes: int
    potential_revenue: float
    waitlist_count: int
    top_waitlist: List[WaitlistCandidate]

class DashboardStatsResponse(BaseModel):
    current_month: MetricSet
    last_year: MetricSet
    total_debt: float
    top_debtors: List[TopDebtor]
    history: List[MonthlyHistory]
    occupancy: OccupancyStats

class ClientCreate(BaseModel):
    name: str
    phone: str
    box_number: int
    status: Optional[str] = "ACTIVE"

    class Config:
        from_attributes = True

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    box_number: Optional[int] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None

    class Config:
        from_attributes = True

class WaitingListCreate(BaseModel):
    name: str
    email: str
    phone: str
    box_type: str
    message: Optional[str] = None

class WaitingListResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    box_size: str
    message: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True

# --- EVENTOS DE INICIO ---
@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def read_root():
    return {"message": "Boat Storage Management API", "status": "running"}

# --- UTILIDADES ---
def get_argentina_date():
    return datetime.now(timezone(timedelta(hours=-3))).date()

# --- ADMIN DEPENDENCY ---
def verify_admin(x_admin_secret: str = Header(None)):
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Contraseña de administrador invalida")
    return True

# --- HELPER FUNCTIONS ---
def get_monthly_fee(db: Session) -> float:
    setting = db.query(SystemSetting).filter(SystemSetting.key == "monthly_fee").first()
    if setting:
        try:
            return float(setting.value)
        except ValueError:
            return DEFAULT_MONTHLY_FEE
    return DEFAULT_MONTHLY_FEE

# --- LÓGICA DE NEGOCIO ---
def calculate_financials(charges: List[MonthlyCharge], monthly_fee: float):
    today = get_argentina_date()
    current_month_start = today.replace(day=1)
    is_before_discount_deadline = today.day < 10
    
    total_debt = 0.0
    has_discount_applied = False
    current_month_base_price = monthly_fee 
    
    sorted_charges = sorted(charges, key=lambda c: c.month_period)
    has_previous_debt = False

    for charge in sorted_charges:
        if charge.status in [ChargeStatus.PENDING, ChargeStatus.PARTIAL]:
            amount = charge.total_amount
            paid_amount = sum(t.amount_paid for t in charge.transactions)
            remaining_debt = amount - paid_amount
            
            if charge.month_period < current_month_start and remaining_debt > 0:
                has_previous_debt = True
            
            if charge.month_period == current_month_start:
                current_month_base_price = charge.total_amount
                
            if charge.month_period == current_month_start and is_before_discount_deadline and not has_previous_debt:
                amount = amount * (1 - DISCOUNT_PERCENTAGE)
                has_discount_applied = True
                remaining_debt = amount - paid_amount
            
            if remaining_debt > 0:
                total_debt += remaining_debt
    
    options = []
    plans = [
        (3, 0.083333334),
        (6, 0.083333334),
        (12, 0.083333334)
    ]
    
    for months, discount in plans:
        base_total = current_month_base_price * months
        final_price = base_total * (1 - discount)
        options.append(PrepaymentOption(
            months=months,
            total_amount=round(final_price, 2),
            savings=round(base_total - final_price, 2)
        ))

    return round(total_debt, 2), has_discount_applied, options


def build_client_response(client: Client, current_debt: float, has_discount: bool, prepayments: List[PrepaymentOption]) -> ClientResponse:
    return ClientResponse(
        id=str(client.id),
        name=client.name,
        phone=client.phone,
        box_number=client.box_number,
        status=client.status.value,
        is_active=client.is_active,
        credit_balance=client.credit_balance,
        charges=[
            ChargeResponse(
                id=str(c.id),
                total_amount=c.total_amount,
                month_period=c.month_period.isoformat(),
                status=c.status.value,
                transactions=[
                    TransactionResponse(
                        id=str(t.id),
                        amount_paid=t.amount_paid,
                        payment_date=t.payment_date,
                        method=t.method.value if t.method else None
                    ) for t in c.transactions
                ]
            ) for c in client.charges
        ],
        current_debt=current_debt,
        has_discount_current_month=has_discount,
        prepayment_options=prepayments
    )


@app.get("/clients/{phone}", response_model=ClientResponse)
@limiter.limit("30/minute")
def get_client_by_phone(request: Request, phone: str, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.phone == phone, Client.is_active == True).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.charges, monthly_fee)
    
    return build_client_response(client, current_debt, discount_applied, prepayments)


@app.post("/webhook/generate-monthly-debt")
def generate_monthly_debt(
    next_month: bool = False,
    x_webhook_secret: str = Header(None),
    db: Session = Depends(get_db)
):
    if WEBHOOK_SECRET and x_webhook_secret != WEBHOOK_SECRET:
         raise HTTPException(status_code=403, detail="Clave de generacion invalida")
    
    active_clients = db.query(Client).filter(Client.status == ClientStatus.ACTIVE, Client.is_active == True).all()
    current_period = get_argentina_date().replace(day=1)
    target_period = current_period + relativedelta(months=1) if next_month else current_period
    
    monthly_fee = get_monthly_fee(db)
    created_count = 0
    auto_paid_count = 0
    
    for client in active_clients:
        existing_charge = db.query(MonthlyCharge).filter(
            MonthlyCharge.client_id == client.id,
            MonthlyCharge.month_period == target_period
        ).first()
        
        if not existing_charge:
            new_charge = MonthlyCharge(
                client_id=client.id,
                total_amount=monthly_fee,
                month_period=target_period,
                status=ChargeStatus.PENDING
            )
            db.add(new_charge)
            db.flush()
            
            created_count += 1
            
            # Autoconsumo de Billetera Virtual
            if client.credit_balance > 0:
                charge_debt = monthly_fee
                today = get_argentina_date()
                
                has_previous_debt = any(
                    c.status in [ChargeStatus.PENDING, ChargeStatus.PARTIAL] 
                    and c.month_period < target_period 
                    for c in client.charges
                )
                
                if not next_month and today.day < 10 and not has_previous_debt:
                    charge_debt = monthly_fee * (1 - DISCOUNT_PERCENTAGE)
                
                amount_to_consume = min(client.credit_balance, charge_debt)
                
                new_transaction = PaymentTransaction(
                    charge_id=new_charge.id,
                    amount_paid=amount_to_consume,
                    method=PaymentMethod.TRANSFER
                )
                db.add(new_transaction)
                
                client.credit_balance -= amount_to_consume
                
                if amount_to_consume >= charge_debt:
                    new_charge.status = ChargeStatus.PAID
                else:
                    new_charge.status = ChargeStatus.PARTIAL
                
                auto_paid_count += 1
                
    db.commit()
    
    return {
        "message": "Proceso completado",
        "period": target_period.isoformat(),
        "charges_created": created_count,
        "auto_paid_charges": auto_paid_count,
        "next_month": next_month
    }

# ========================================
# --- ADMIN ENDPOINTS ---
# ========================================

@app.get("/admin/settings/fee", response_model=FeeResponse)
@limiter.limit("10/minute")
def get_monthly_fee_admin(request: Request, db: Session = Depends(get_db)):
    x_admin_secret = request.headers.get("x-admin-secret")
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Contraseña de administrador invalida")
    
    setting = db.query(SystemSetting).filter(SystemSetting.key == "monthly_fee").first()
    if not setting:
        raise HTTPException(status_code=404, detail="Cuota mensual no configurada")
    return FeeResponse(key=setting.key, value=setting.value)


@app.post("/admin/settings/fee")
@limiter.limit("10/minute")
def update_monthly_fee(request: Request, fee_update: FeeUpdate, db: Session = Depends(get_db)):
    x_admin_secret = request.headers.get("x-admin-secret")
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Contraseña de administrador invalida")
        
    if fee_update.fee <= 0:
        raise HTTPException(status_code=400, detail="La cuota debe ser mayor que cero")
    
    setting = db.query(SystemSetting).filter(SystemSetting.key == "monthly_fee").first()
    if setting:
        setting.value = str(fee_update.fee)
    else:
        setting = SystemSetting(key="monthly_fee", value=str(fee_update.fee))
        db.add(setting)
    
    db.commit()
    
    pending_charges = db.query(MonthlyCharge).filter(MonthlyCharge.status == ChargeStatus.PENDING).all()
    for charge in pending_charges:
        charge.total_amount = fee_update.fee
    
    db.commit()
    
    return {
        "message": "Cuota mensual actualizada y cuotas pendientes recalculadas",
        "key": setting.key,
        "value": setting.value,
        "charges_updated": len(pending_charges)
    }


@app.post("/admin/clients", response_model=ClientResponse)
def create_client(client_data: ClientCreate, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    existing_client = db.query(Client).filter(Client.phone == client_data.phone).first()
    if existing_client:
        raise HTTPException(status_code=400, detail="Celular ya registrado")
    
    new_client = Client(
        name=client_data.name,
        phone=client_data.phone,
        box_number=client_data.box_number,
        status=ClientStatus(client_data.status) if client_data.status else ClientStatus.ACTIVE,
        is_active=True,
        credit_balance=0.0
    )
    
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials([], monthly_fee)
    
    return build_client_response(new_client, current_debt, discount_applied, prepayments)


@app.get("/admin/clients/{client_id}", response_model=ClientResponse)
def get_client_admin(client_id: str, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.charges, monthly_fee)
    
    return build_client_response(client, current_debt, discount_applied, prepayments)


@app.get("/admin/clients", response_model=List[ClientResponse])
def get_all_clients(
    is_active: str = Query("true", description="Filtrar por clientes activos/inactivos: 'true', 'false', 'all'. Default: 'true'"),
    db: Session = Depends(get_db), 
    _: str = Depends(verify_admin)
):
    query = db.query(Client).options(joinedload(Client.charges).joinedload(MonthlyCharge.transactions))
    
    val = is_active.lower()
    if val == "true":
        query = query.filter(Client.is_active == True)
    elif val == "false":
        query = query.filter(Client.is_active == False)
        
    clients = query.all()
    current_fee = get_monthly_fee(db)
    
    response_list = []
    for client in clients:
        current_debt, discount_applied, prepayments = calculate_financials(client.charges, current_fee)
        response_list.append(build_client_response(client, current_debt, discount_applied, prepayments))
    
    return response_list


@app.put("/admin/clients/{client_id}", response_model=ClientResponse)
def update_client(client_id: str, client_data: ClientUpdate, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    if client_data.phone and client_data.phone != client.phone:
        existing_client = db.query(Client).filter(Client.phone == client_data.phone).first()
        if existing_client:
            raise HTTPException(status_code=400, detail="Celular ya registrado")
        client.phone = client_data.phone
    
    if client_data.name is not None:
        client.name = client_data.name
    if client_data.box_number is not None:
        client.box_number = client_data.box_number
    if client_data.status is not None:
        client.status = ClientStatus(client_data.status)
    if client_data.is_active is not None:
        client.is_active = client_data.is_active
    
    db.commit()
    db.refresh(client)
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.charges, monthly_fee)
    
    return build_client_response(client, current_debt, discount_applied, prepayments)


@app.delete("/admin/clients/{client_id}")
def delete_client(client_id: str, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    charges = db.query(MonthlyCharge).filter(MonthlyCharge.client_id == client_id).all()
    for charge in charges:
        db.query(PaymentTransaction).filter(PaymentTransaction.charge_id == charge.id).delete()
    
    db.query(MonthlyCharge).filter(MonthlyCharge.client_id == client_id).delete()
    db.delete(client)
    db.commit()
    
    return {"message": "Cliente eliminado exitosamente", "client_id": client_id}


@app.get("/admin/dashboard-stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    today = get_argentina_date()
    current_month_start = today.replace(day=1)
    
    # 1. Current Month Stats
    invoiced_cm = db.query(func.sum(MonthlyCharge.total_amount)).filter(
        MonthlyCharge.month_period == current_month_start
    ).scalar() or 0.0
    
    transactions_cm = db.query(
        PaymentTransaction.method, 
        func.sum(PaymentTransaction.amount_paid)
    ).join(MonthlyCharge).filter(
        MonthlyCharge.month_period == current_month_start
    ).group_by(PaymentTransaction.method).all()
    
    cash_cm = 0.0
    transfer_cm = 0.0
    for method, amount in transactions_cm:
        if method == PaymentMethod.CASH:
            cash_cm += amount
        else:
            transfer_cm += amount
    paid_cm = cash_cm + transfer_cm
    
    current_month_stats = MetricSet(
        invoiced=invoiced_cm,
        paid=paid_cm,
        cash=cash_cm,
        transfer=transfer_cm
    )
    
    # 2. Last Year Stats (Last 12 months)
    one_year_ago = current_month_start - relativedelta(months=11)
    
    invoiced_ly = db.query(func.sum(MonthlyCharge.total_amount)).filter(
        MonthlyCharge.month_period >= one_year_ago
    ).scalar() or 0.0
    
    transactions_ly = db.query(
        PaymentTransaction.method, 
        func.sum(PaymentTransaction.amount_paid)
    ).join(MonthlyCharge).filter(
        MonthlyCharge.month_period >= one_year_ago
    ).group_by(PaymentTransaction.method).all()
    
    cash_ly = 0.0
    transfer_ly = 0.0
    for method, amount in transactions_ly:
        if method == PaymentMethod.CASH:
            cash_ly += amount
        else:
            transfer_ly += amount
    paid_ly = cash_ly + transfer_ly
    
    last_year_stats = MetricSet(
        invoiced=invoiced_ly,
        paid=paid_ly,
        cash=cash_ly,
        transfer=transfer_ly
    )
    
    # 3. Total Debt & Top Debtors
    clients = db.query(Client).filter(Client.is_active == True).options(joinedload(Client.charges).joinedload(MonthlyCharge.transactions)).all()
    monthly_fee = get_monthly_fee(db)
    
    total_debt = 0.0
    debtor_list = []
    
    for client in clients:
        current_debt, _, _ = calculate_financials(client.charges, monthly_fee)
        real_debt = max(0, current_debt - client.credit_balance)
        total_debt += real_debt
        
        if real_debt > 0:
            debtor_list.append(TopDebtor(name=client.name, phone=client.phone, debt=real_debt))
            
    debtor_list.sort(key=lambda x: x.debt, reverse=True)
    top_debtors = debtor_list[:5]
    
    # 4. Monthly History (Last 12 months)
    twelve_months_ago = current_month_start - relativedelta(months=11)
    history_data = []
    
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    
    for i in range(12):
        month_cursor = twelve_months_ago + relativedelta(months=i)
        month_revenue = db.query(func.sum(PaymentTransaction.amount_paid)).join(MonthlyCharge).filter(
            MonthlyCharge.month_period == month_cursor
        ).scalar() or 0.0
        
        month_str = f"{month_names[month_cursor.month - 1]} {month_cursor.year}"
        history_data.append(MonthlyHistory(month=month_str, revenue=month_revenue))
        
    # 5. Occupancy & Operations Stats
    total_rentable_boxes = 26
    
    occupied_boxes = db.query(Client).filter(
        Client.is_active == True,
        Client.box_number > 3
    ).count()
    
    available_boxes = max(0, total_rentable_boxes - occupied_boxes)
    occupancy_rate = (occupied_boxes / total_rentable_boxes) * 100 if total_rentable_boxes > 0 else 0
    potential_revenue = total_rentable_boxes * monthly_fee
    
    waitlist_count = db.query(WaitingList).count()
    top_waitlist_db = db.query(WaitingList).order_by(WaitingList.created_at.asc()).limit(3).all()
    top_waitlist = [WaitlistCandidate(name=w.name, phone=w.phone, box_size=w.box_type) for w in top_waitlist_db]
    
    occupancy_stats = OccupancyStats(
        occupancy_rate=round(occupancy_rate, 2),
        available_boxes=available_boxes,
        occupied_boxes=occupied_boxes,
        total_rentable_boxes=total_rentable_boxes,
        potential_revenue=potential_revenue,
        waitlist_count=waitlist_count,
        top_waitlist=top_waitlist
    )
        
    return DashboardStatsResponse(
        current_month=current_month_stats,
        last_year=last_year_stats,
        total_debt=total_debt,
        top_debtors=top_debtors,
        history=history_data,
        occupancy=occupancy_stats
    )

# --- ADMIN CHARGE ENDPOINTS ---
@app.post("/admin/charges", response_model=ClientResponse)
def create_manual_charge(
    charge_data: ChargeCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    client = db.query(Client).filter(Client.id == charge_data.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
        
    existing_charge = db.query(MonthlyCharge).filter(
        MonthlyCharge.client_id == charge_data.client_id,
        MonthlyCharge.month_period == charge_data.month_period
    ).first()
    
    if existing_charge:
        raise HTTPException(status_code=400, detail="Ya existe una cuota para este periodo")
        
    if charge_data.total_amount <= 0:
        raise HTTPException(status_code=400, detail="El monto de la cuota debe ser mayor a 0")
        
    new_charge = MonthlyCharge(
        client_id=client.id,
        total_amount=charge_data.total_amount,
        month_period=charge_data.month_period,
        status=ChargeStatus.PENDING
    )
    db.add(new_charge)
    db.flush()
    
    if client.credit_balance > 0:
        amount_to_consume = min(client.credit_balance, charge_data.total_amount)
        new_transaction = PaymentTransaction(
            charge_id=new_charge.id,
            amount_paid=amount_to_consume,
            method=PaymentMethod.TRANSFER
        )
        db.add(new_transaction)
        client.credit_balance -= amount_to_consume
        if amount_to_consume >= charge_data.total_amount:
            new_charge.status = ChargeStatus.PAID
        else:
            new_charge.status = ChargeStatus.PARTIAL

    db.commit()
    db.refresh(client)
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.charges, monthly_fee)
    
    return build_client_response(client, current_debt, discount_applied, prepayments)

@app.post("/admin/clients/{client_id}/transactions")
def create_client_transaction(
    client_id: str,
    tx_data: ClientTransactionCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    if tx_data.amount_paid <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor que cero")
        
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
        
    try:
        method = PaymentMethod(tx_data.method)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Método inválido. Debe ser uno de: {', '.join([m.value for m in PaymentMethod])}")

    today = get_argentina_date()
    current_month_start = today.replace(day=1)
    is_before_discount_deadline = today.day < 10
    
    pending_charges = [c for c in client.charges if c.status in [ChargeStatus.PENDING, ChargeStatus.PARTIAL]]
    sorted_charges = sorted(pending_charges, key=lambda c: c.month_period)
    
    remaining_payment = tx_data.amount_paid
    created_txs = []
    
    has_previous_debt = False
    for charge in sorted_charges:
        paid_so_far = sum(t.amount_paid for t in charge.transactions)
        remaining_debt = charge.total_amount - paid_so_far
        if charge.month_period < current_month_start and remaining_debt > 0:
            has_previous_debt = True
            break
            
    for charge in sorted_charges:
        if remaining_payment <= 0:
            break
            
        current_charge_debt = charge.total_amount
        if charge.month_period == current_month_start and is_before_discount_deadline and not has_previous_debt:
            current_charge_debt = current_charge_debt * (1 - DISCOUNT_PERCENTAGE)
            
        paid_so_far = sum(t.amount_paid for t in charge.transactions)
        remaining_debt = current_charge_debt - paid_so_far
        
        if remaining_debt <= 0:
            continue
            
        amount_to_apply = min(remaining_payment, remaining_debt)
        
        new_tx = PaymentTransaction(
            charge_id=charge.id,
            amount_paid=amount_to_apply,
            method=method
        )
        db.add(new_tx)
        created_txs.append(new_tx)
        
        if amount_to_apply >= remaining_debt:
            charge.status = ChargeStatus.PAID
        else:
            charge.status = ChargeStatus.PARTIAL
            
        remaining_payment -= amount_to_apply
        
    if remaining_payment > 0:
        client.credit_balance += remaining_payment
        
    db.commit()
    
    return {
        "message": "Pago registrado exitosamente",
        "applied_transactions": len(created_txs),
        "new_credit_balance": client.credit_balance
    }

# --- ADMIN TRANSACTION ENDPOINTS ---
@app.post("/admin/transactions")
def create_transaction(
    tx_data: TransactionCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    if tx_data.amount_paid <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor que cero")
    
    charge = db.query(MonthlyCharge).filter(MonthlyCharge.id == tx_data.charge_id).first()
    if not charge:
        raise HTTPException(status_code=404, detail="Cuota no encontrada")
        
    try:
        method = PaymentMethod(tx_data.method)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Método inválido. Debe ser uno de: {', '.join([m.value for m in PaymentMethod])}")

    today = get_argentina_date()
    current_month_start = today.replace(day=1)
    is_before_discount_deadline = today.day < 10
    
    current_charge_debt = charge.total_amount
    if charge.month_period == current_month_start and is_before_discount_deadline:
        current_charge_debt = current_charge_debt * (1 - DISCOUNT_PERCENTAGE)
        
    paid_so_far = sum(t.amount_paid for t in charge.transactions)
    remaining_debt = current_charge_debt - paid_so_far
    
    new_tx = PaymentTransaction(
        charge_id=charge.id,
        amount_paid=tx_data.amount_paid,
        method=method
    )
    db.add(new_tx)
    
    client = charge.client
    
    if tx_data.amount_paid >= remaining_debt:
        charge.status = ChargeStatus.PAID
        excess = tx_data.amount_paid - remaining_debt
        if excess > 0:
            client.credit_balance += excess
    else:
        charge.status = ChargeStatus.PARTIAL
        
    db.commit()
    db.refresh(new_tx)
    db.refresh(charge)
    
    return {
        "message": "Transacción registrada exitosamente",
        "transaction_id": str(new_tx.id),
        "charge_status": charge.status.value,
        "new_credit_balance": client.credit_balance
    }

@app.delete("/admin/transactions/{tx_id}")
def delete_transaction(tx_id: str, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.id == tx_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")
        
    charge = tx.charge
    client = charge.client
    
    today = get_argentina_date()
    current_month_start = today.replace(day=1)
    is_before_discount_deadline = today.day < 10
    
    current_charge_debt = charge.total_amount
    if charge.month_period == current_month_start and is_before_discount_deadline:
        current_charge_debt = current_charge_debt * (1 - DISCOUNT_PERCENTAGE)
        
    total_paid_before = sum(t.amount_paid for t in charge.transactions)
    total_paid_after = total_paid_before - tx.amount_paid
    
    excess_before = max(0.0, total_paid_before - current_charge_debt)
    excess_after = max(0.0, total_paid_after - current_charge_debt)
    
    excess_to_remove = excess_before - excess_after
    if excess_to_remove > 0:
        client.credit_balance -= excess_to_remove
        
    if total_paid_after >= current_charge_debt:
        charge.status = ChargeStatus.PAID
    elif total_paid_after > 0:
        charge.status = ChargeStatus.PARTIAL
    else:
        charge.status = ChargeStatus.PENDING
        
    db.delete(tx)
    db.commit()
    
    return {
        "message": "Transacción eliminada exitosamente",
        "charge_status": charge.status.value,
        "new_credit_balance": client.credit_balance
    }

# --- ADMIN WAITING LIST ENDPOINTS ---
@app.get("/admin/waiting-list", response_model=List[WaitingListResponse])
def get_waiting_list(db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    entries = db.query(WaitingList).order_by(WaitingList.created_at.desc()).all()
    result = []
    for entry in entries:
        result.append(WaitingListResponse(
            id=entry.id,
            name=entry.name,
            email=entry.email,
            phone=entry.phone,
            box_size=entry.box_type,
            message=entry.message,
            created_at=entry.created_at.isoformat() if entry.created_at else None
        ))
    return result

@app.delete("/admin/waiting-list/{entry_id}")
def delete_waiting_list_entry(entry_id: str, db: Session = Depends(get_db), _: bool = Depends(verify_admin)):
    entry = db.query(WaitingList).filter(WaitingList.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entrada de lista de espera no encontrada")
    db.delete(entry)
    db.commit()
    return {"message": "Entrada de lista de espera eliminada exitosamente", "entry_id": entry_id}

@app.post("/waiting-list")
@limiter.limit("5/minute")
def create_waiting_list_entry(request: Request, entry: WaitingListCreate, db: Session = Depends(get_db)):
    new_entry = WaitingList(
        name=entry.name,
        email=entry.email,
        phone=entry.phone,
        box_type=entry.box_type,
        message=entry.message
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return {"message": "Agregado a la lista de espera exitosamente", "id": new_entry.id}
