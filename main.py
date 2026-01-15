from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware  # <--- IMPORTANTE
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import List, Optional
from pydantic import BaseModel
import os

from database import get_db, init_db
from models import Client, Payment, ClientStatus, PaymentStatus


app = FastAPI(title="Boat Storage Management API")

# --- CONFIGURACIÓN DE CORS ---
# Permite que tu Frontend (localhost o Vercel) hable con el Backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, cámbialo por tu dominio de Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "my-secret-key")
MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "100.0"))
DISCOUNT_PERCENTAGE = 0.10

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

# --- EVENTOS DE INICIO ---
@app.on_event("startup")
def on_startup():
    init_db()

# --- EVENTOS DE INICIO ---
@app.get("/")
def read_root():
    return {"message": "Boat Storage Management API", "status": "running"}


# --- LÓGICA DE NEGOCIO ---
def calculate_financials(payments: List[Payment]):
    """Calcula deuda real (descuento solo en mes actual) y opciones de pago adelantado"""
    today = date.today()
    # Primer día del mes actual para comparar
    current_month_start = today.replace(day=1)
    
    # El descuento aplica si hoy es menor al día 10
    is_before_discount_deadline = today.day < 25
    
    total_debt = 0.0
    has_discount_applied = False

    for payment in payments:
        if payment.status == PaymentStatus.PENDING:
            amount = payment.amount
            
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
        (3, 0.05),  # 3 meses, 5% descuento
        (6, 0.10),  # 6 meses, 10% descuento
        (12, 0.15)  # 12 meses, 15% descuento
    ]
    
    for months, discount in plans:
        base_total = MONTHLY_FEE * months
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
        raise HTTPException(status_code=404, detail="Client not found")
    
    current_debt, discount_applied, prepayments = calculate_financials(client.payments)
    
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
    x_webhook_secret: str = Header(None), # Header opcional para pruebas locales faciles
    db: Session = Depends(get_db)
):
    # Validación simple de seguridad
    if WEBHOOK_SECRET and x_webhook_secret != WEBHOOK_SECRET:
         raise HTTPException(status_code=403, detail="Invalid webhook secret")
    
    active_clients = db.query(Client).filter(Client.status == ClientStatus.ACTIVE).all()
    current_period = date.today().replace(day=1)
    
    created_count = 0
    
    for client in active_clients:
        # Verificar si ya existe la cuota de este mes
        existing_payment = db.query(Payment).filter(
            Payment.client_id == client.id,
            Payment.month_period == current_period
        ).first()
        
        if not existing_payment:
            new_payment = Payment(
                client_id=client.id,
                amount=MONTHLY_FEE,
                month_period=current_period,
                status=PaymentStatus.PENDING
            )
            db.add(new_payment)
            created_count += 1
    
    db.commit()
    
    return {
        "message": "Proceso completado",
        "period": current_period.isoformat(),
        "payments_created": created_count
    }