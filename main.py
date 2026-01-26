from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware  # <--- IMPORTANTE
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import os

from database import get_db, init_db
from models import Client, Payment, ClientStatus, PaymentStatus, SystemSetting, PaymentMethod, WaitingList


app = FastAPI(title="Boat Storage Management API")

# --- CONFIGURACIÓN DE CORS ---
# Permite que tu Frontend (localhost o Vercel) hable con el Backend
origins = [
    "http://localhost:3000",              # Mantenlo para poder seguir desarrollando en tu PC
    "https://guarderialachueca.com"  # <--- PEGA AQUÍ EL VALOR EXACTO QUE COPIASTE
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,      # <--- Usamos la variable 'origins', NO uses ["*"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ADMIN_SECRET = os.getenv("ADMIN_SECRET")
DEFAULT_MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "100.0"))
DISCOUNT_PERCENTAGE = 0.08

# --- SCHEMAS (Modelos de respuesta) ---
class PaymentResponse(BaseModel):
    id: str
    amount: float
    month_period: str
    status: str
    method: Optional[str]

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
    payments: List[PaymentResponse]
    current_debt: float
    has_discount_current_month: bool # Renombrado para ser preciso
    prepayment_options: List[PrepaymentOption] # Nuevo campo

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

class PaymentUpdate(BaseModel):
    amount: Optional[float] = None
    status: Optional[str] = None
    method: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "amount": 50.0,
                "status": "PAID",
                "method": "TRANSFER"
            }
        }

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

# --- EVENTOS DE INICIO ---
@app.get("/")
def read_root():
    return {"message": "Boat Storage Management API", "status": "running"}

# --- UTILIDADES ---
def get_argentina_date():
    # UTC menos 3 horas
    return datetime.now(timezone(timedelta(hours=-3))).date()

# --- ADMIN DEPENDENCY ---
def verify_admin(x_admin_secret: str = Header(None)):
    """Verifies the admin secret header."""
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Contraseña de administrador invalida")
    return True


# --- HELPER FUNCTIONS ---
def get_monthly_fee(db: Session) -> float:
    """
    Retrieves the monthly fee from the SystemSetting table.
    Falls back to DEFAULT_MONTHLY_FEE if not found.
    """
    setting = db.query(SystemSetting).filter(
        SystemSetting.key == "monthly_fee"
    ).first()
    
    if setting:
        try:
            return float(setting.value)
        except ValueError:
            return DEFAULT_MONTHLY_FEE
    
    return DEFAULT_MONTHLY_FEE


# --- LÓGICA DE NEGOCIO ---
def calculate_financials(payments: List[Payment], monthly_fee: float):
    """Calcula deuda real (descuento solo en mes actual) y opciones de pago adelantado"""
    today = get_argentina_date()
    # Primer día del mes actual para comparar
    current_month_start = today.replace(day=1)
    
    # El descuento aplica si hoy es menor al día 10
    is_before_discount_deadline = today.day < 10
    
    total_debt = 0.0
    has_discount_applied = False

    current_month_base_price = monthly_fee #nuevo

    for payment in payments:
        if payment.status == PaymentStatus.PENDING:
            amount = payment.amount
            
            if payment.month_period == current_month_start: #nuevo
                current_month_base_price = payment.amount #nuevo
            # LÓGICA CORREGIDA:
            # Solo aplicamos descuento si la cuota es de ESTE mes 
            # Y estamos antes del día 10.
            if payment.month_period == current_month_start and is_before_discount_deadline:
                amount = amount * (1 - DISCOUNT_PERCENTAGE)
                has_discount_applied = True
            
            total_debt += amount
    
    # Calculadora de Pagos Adelantados (Precio Base * Meses * Descuento especial)
    # Ejemplo: 3 meses 5% off, 6 meses 10% off, 12 meses 15% off
    options = []
    plans = [
        (3, 0.083333334),  # 3 meses, 5% descuento
        (6, 0.083333334),  # 6 meses, 10% descuento
        (12, 0.083333334)  # 12 meses, 15% descuento
    ]
    
    for months, discount in plans:
        base_total = current_month_base_price * months #nuevo
        final_price = base_total * (1 - discount)
        options.append(PrepaymentOption(
            months=months,
            total_amount=round(final_price, 2),
            savings=round(base_total - final_price, 2)
        ))

    return round(total_debt, 2), has_discount_applied, options


@app.get("/clients/{phone}", response_model=ClientResponse)
def get_client_by_phone(phone: str, db: Session = Depends(get_db)):
    # Buscamos por teléfono (o podrías agregar lógica para ID también)
    client = db.query(Client).filter(Client.phone == phone).first()
    
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.payments, monthly_fee)
    
    return ClientResponse(
        id=str(client.id),
        name=client.name,
        phone=client.phone,
        box_number=client.box_number,
        status=client.status.value,
        payments=[
            PaymentResponse(
                id=str(p.id),
                amount=p.amount,
                month_period=p.month_period.isoformat(),
                status=p.status.value,
                method=p.method.value if p.method else None
            )
            for p in client.payments
        ],
        current_debt=current_debt,
        has_discount_current_month=discount_applied,
        prepayment_options=prepayments
    )


@app.post("/webhook/generate-monthly-debt")
def generate_monthly_debt(
    next_month: bool = False,  # Nuevo parámetro opcional
    x_webhook_secret: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Genera deudas mensuales para clientes activos.
    
    Args:
        next_month: Si es True, genera deudas para el próximo mes. 
                   Si es False (default), genera para el mes actual.
    """
    # Validación simple de seguridad
    if WEBHOOK_SECRET and x_webhook_secret != WEBHOOK_SECRET:
         raise HTTPException(status_code=403, detail="Clave de generacion invalida")
    
    active_clients = db.query(Client).filter(Client.status == ClientStatus.ACTIVE).all()
    current_period = get_argentina_date().replace(day=1)
    
    # Determinar el período según el parámetro
    target_period = current_period + relativedelta(months=1) if next_month else current_period
    
    monthly_fee = get_monthly_fee(db)
    created_count = 0
    
    for client in active_clients:
        # Verificar si ya existe la cuota para el período objetivo
        existing_payment = db.query(Payment).filter(
            Payment.client_id == client.id,
            Payment.month_period == target_period
        ).first()
        
        if not existing_payment:
            new_payment = Payment(
                client_id=client.id,
                amount=monthly_fee,
                month_period=target_period,
                status=PaymentStatus.PENDING
            )
            db.add(new_payment)
            created_count += 1
    
    db.commit()
    
    return {
        "message": "Proceso completado",
        "period": target_period.isoformat(),
        "payments_created": created_count,
        "next_month": next_month
    }

# ========================================
# --- ADMIN ENDPOINTS ---
# ========================================

# --- ADMIN SETTINGS ENDPOINTS ---
@app.get("/admin/settings/fee", response_model=FeeResponse)
def get_monthly_fee_admin(
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Get the current monthly fee from system settings."""
    setting = db.query(SystemSetting).filter(
        SystemSetting.key == "monthly_fee"
    ).first()
    
    if not setting:
        raise HTTPException(status_code=404, detail="Cuota mensual no configurada")
    
    return FeeResponse(key=setting.key, value=setting.value)


@app.post("/admin/settings/fee")
def update_monthly_fee(
    fee_update: FeeUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Update the monthly fee in system settings."""
    if fee_update.fee <= 0:
        raise HTTPException(status_code=400, detail="La cuota debe ser mayor que cero")
    
    setting = db.query(SystemSetting).filter(
        SystemSetting.key == "monthly_fee"
    ).first()
    
    if setting:
        setting.value = str(fee_update.fee)
    else:
        setting = SystemSetting(key="monthly_fee", value=str(fee_update.fee))
        db.add(setting)
    
    db.commit()
    db.refresh(setting)
    
    # Recalculate all PENDING payments with the new fee
    pending_payments = db.query(Payment).filter(
        Payment.status == PaymentStatus.PENDING
    ).all()
    
    for payment in pending_payments:
        payment.amount = fee_update.fee
    
    db.commit()
    
    return {
        "message": "Cuota mensual actualizada y pagos pendientes recalculados",
        "key": setting.key,
        "value": setting.value,
        "payments_updated": len(pending_payments)
    }


# --- ADMIN CLIENT CRUD ENDPOINTS ---
@app.post("/admin/clients", response_model=ClientResponse)
def create_client(
    client_data: ClientCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Create a new client."""
    # Check if phone already exists
    existing_client = db.query(Client).filter(Client.phone == client_data.phone).first()
    if existing_client:
        raise HTTPException(status_code=400, detail="Celular ya registrado")
    
    # Create new client
    new_client = Client(
        name=client_data.name,
        phone=client_data.phone,
        box_number=client_data.box_number,
        status=ClientStatus(client_data.status) if client_data.status else ClientStatus.ACTIVE
    )
    
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(new_client.payments, monthly_fee)
    
    return ClientResponse(
        id=str(new_client.id),
        name=new_client.name,
        phone=new_client.phone,
        box_number=new_client.box_number,
        status=new_client.status.value,
        payments=[],
        current_debt=current_debt,
        has_discount_current_month=discount_applied,
        prepayment_options=prepayments
    )


@app.get("/admin/clients/{client_id}", response_model=ClientResponse)
def get_client_admin(
    client_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Get client details by ID (admin)."""
    client = db.query(Client).filter(Client.id == client_id).first()
    
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.payments, monthly_fee)
    
    return ClientResponse(
        id=str(client.id),
        name=client.name,
        phone=client.phone,
        box_number=client.box_number,
        status=client.status.value,
        payments=[
            PaymentResponse(
                id=str(p.id),
                amount=p.amount,
                month_period=p.month_period.isoformat(),
                status=p.status.value,
                method=p.method.value if p.method else None
            )
            for p in client.payments
        ],
        current_debt=current_debt,
        has_discount_current_month=discount_applied,
        prepayment_options=prepayments
    )

# --- AGREGAR ESTO EN main.py (Sección Admin) ---

# 4. 📢 ADMIN: Listar TODOS los clientes
@app.get("/admin/clients", response_model=List[ClientResponse])
def get_all_clients(db: Session = Depends(get_db), _: str = Depends(verify_admin)):
    clients = db.query(Client).options(joinedload(Client.payments)).all()
    # Procesamos la respuesta igual que en el endpoint individual
    current_fee = get_monthly_fee(db)
    
    response_list = []
    for client in clients:
        current_debt, discount_applied, prepayments = calculate_financials(client.payments, current_fee)
        
        response_list.append(ClientResponse(
            id=str(client.id),
            name=client.name,
            phone=client.phone,
            box_number=client.box_number,
            status=client.status.value,
            payments=[
                PaymentResponse(
                    id=str(p.id),
                    amount=p.amount,
                    month_period=p.month_period.isoformat(),
                    status=p.status.value,
                    method=p.method.value if p.method else None
                ) for p in client.payments
            ],
            current_debt=current_debt,
            has_discount_current_month=discount_applied,
            prepayment_options=prepayments
        ))
    
    return response_list

@app.put("/admin/clients/{client_id}", response_model=ClientResponse)
def update_client(
    client_id: str,
    client_data: ClientUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Update client information."""
    client = db.query(Client).filter(Client.id == client_id).first()
    
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    # Check if new phone is unique
    if client_data.phone and client_data.phone != client.phone:
        existing_client = db.query(Client).filter(Client.phone == client_data.phone).first()
        if existing_client:
            raise HTTPException(status_code=400, detail="Celular ya registrado")
        client.phone = client_data.phone
    
    if client_data.name:
        client.name = client_data.name
    
    if client_data.box_number is not None:
        client.box_number = client_data.box_number
    
    if client_data.status:
        client.status = ClientStatus(client_data.status)
    
    db.commit()
    db.refresh(client)
    
    monthly_fee = get_monthly_fee(db)
    current_debt, discount_applied, prepayments = calculate_financials(client.payments, monthly_fee)
    
    return ClientResponse(
        id=str(client.id),
        name=client.name,
        phone=client.phone,
        box_number=client.box_number,
        status=client.status.value,
        payments=[
            PaymentResponse(
                id=str(p.id),
                amount=p.amount,
                month_period=p.month_period.isoformat(),
                status=p.status.value,
                method=p.method.value if p.method else None
            )
            for p in client.payments
        ],
        current_debt=current_debt,
        has_discount_current_month=discount_applied,
        prepayment_options=prepayments
    )


@app.delete("/admin/clients/{client_id}")
def delete_client(
    client_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Delete a client and all associated payments."""
    client = db.query(Client).filter(Client.id == client_id).first()
    
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    # Delete associated payments
    db.query(Payment).filter(Payment.client_id == client_id).delete()
    
    # Delete client
    db.delete(client)
    db.commit()
    
    return {"message": "Cliente eliminado exitosamente", "client_id": client_id}


# --- ADMIN PAYMENT ENDPOINTS ---
@app.patch("/admin/payments/{payment_id}")
def partial_update_payment(
    payment_id: str,
    payment_update: PaymentUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Partially update a payment (PATCH endpoint for partial updates)."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    
    # Update amount if provided
    if payment_update.amount is not None:
        if payment_update.amount < 0:
            raise HTTPException(status_code=400, detail="El monto no puede ser negativo")
        payment.amount = payment_update.amount
    
    # Update status if provided
    if payment_update.status:
        try:
            payment.status = PaymentStatus(payment_update.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Estado inválido. Debe ser uno de: {', '.join([s.value for s in PaymentStatus])}")
    
    # Update method if provided
    if payment_update.method:
        try:
            payment.method = PaymentMethod(payment_update.method)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Método inválido. Debe ser uno de: {', '.join([m.value for m in PaymentMethod])}")
    
    db.commit()
    db.refresh(payment)
    
    return {
        "message": "Pago actualizado exitosamente",
        "payment": {
            "id": str(payment.id),
            "client_id": str(payment.client_id),
            "amount": payment.amount,
            "month_period": payment.month_period.isoformat(),
            "status": payment.status.value,
            "method": payment.method.value if payment.method else None
        }
    }


@app.get("/admin/payments/{payment_id}")
def get_payment_admin(
    payment_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Get payment details by ID (admin)."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    
    return {
        "id": str(payment.id),
        "client_id": str(payment.client_id),
        "amount": payment.amount,
        "month_period": payment.month_period.isoformat(),
        "status": payment.status.value,
        "method": payment.method.value if payment.method else None
    }


@app.delete("/admin/payments/{payment_id}")
def delete_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Delete a payment record."""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    
    db.delete(payment)
    db.commit()
    
    return {"message": "Pago eliminado exitosamente", "payment_id": payment_id}


@app.post("/admin/payments")
def create_payment(
    payment_data: dict,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Create a new payment manually (admin)."""
    required_fields = ["client_id", "amount", "month_period", "status"]
    
    for field in required_fields:
        if field not in payment_data:
            raise HTTPException(status_code=400, detail=f"Falta el campo requerido: {field}")
    
    # Validate client exists
    client = db.query(Client).filter(Client.id == payment_data["client_id"]).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    # Validate amount
    if payment_data["amount"] < 0:
        raise HTTPException(status_code=400, detail="El monto no puede ser negativo")
    
    # Validate status
    try:
        status = PaymentStatus(payment_data["status"])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Debe ser uno de: {', '.join([s.value for s in PaymentStatus])}")
    
    # Validate method if provided
    method = None
    if "method" in payment_data and payment_data["method"]:
        try:
            method = PaymentMethod(payment_data["method"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Método inválido. Debe ser uno de: {', '.join([m.value for m in PaymentMethod])}")
    
    # Parse month_period
    try:
        month_period = date.fromisoformat(payment_data["month_period"])
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Use YYYY-MM-DD")
    
    new_payment = Payment(
        client_id=payment_data["client_id"],
        amount=payment_data["amount"],
        month_period=month_period,
        status=status,
        method=method
    )
    
    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)
    
    return {
        "message": "Pago creado exitosamente",
        "payment": {
            "id": str(new_payment.id),
            "client_id": str(new_payment.client_id),
            "amount": new_payment.amount,
            "month_period": new_payment.month_period.isoformat(),
            "status": new_payment.status.value,
            "method": new_payment.method.value if new_payment.method else None
        }
    }

# --- ADMIN WAITING LIST ENDPOINTS ---
@app.get("/admin/waiting-list", response_model=List[WaitingListResponse])
def get_waiting_list(
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Get all waiting list entries."""
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
def delete_waiting_list_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_admin)
):
    """Delete a waiting list entry."""
    entry = db.query(WaitingList).filter(WaitingList.id == entry_id).first()
    
    if not entry:
        raise HTTPException(status_code=404, detail="Entrada de lista de espera no encontrada")
    
    db.delete(entry)
    db.commit()
    
    return {"message": "Entrada de lista de espera eliminada exitosamente", "entry_id": entry_id}

@app.post("/waiting-list")
def create_waiting_list_entry(
    entry: WaitingListCreate,
    db: Session = Depends(get_db)
):
    """Registra un nuevo interesado en la lista de espera."""
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
