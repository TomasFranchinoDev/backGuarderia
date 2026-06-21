from sqlalchemy import Column, String, Integer, Float, Date, Enum, ForeignKey, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from database import Base

# --- ENUMS ---
class ClientStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEBTOR = "DEBTOR"
    INACTIVE = "INACTIVE" # Opcional: Para clientes que se dan de baja

class ChargeStatus(str, enum.Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL" # Nuevo: Para cuotas pagadas a medias
    PAID = "PAID"

class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"


# --- TABLAS ---
class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    box_number = Column(Integer, nullable=False)
    status = Column(Enum(ClientStatus), default=ClientStatus.ACTIVE, nullable=False)
    
    # Mejora: Auditoría
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_active = Column(Boolean, default=True) # Mejora: Soft delete

    # Relaciones
    charges = relationship("MonthlyCharge", back_populates="client")

    credit_balance = Column(Float, default=0.0, nullable=False)


class MonthlyCharge(Base):
    """Representa la DEUDA o CUOTA generada cada mes."""
    __tablename__ = "monthly_charges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    
    total_amount = Column(Float, nullable=False) # El monto original de la cuota (Ej: $50,000)
    month_period = Column(Date, nullable=False)
    status = Column(Enum(ChargeStatus), default=ChargeStatus.PENDING, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    client = relationship("Client", back_populates="charges")
    transactions = relationship("PaymentTransaction", back_populates="charge")


class PaymentTransaction(Base):
    """Representa el PAGO FÍSICO o TRANSFERENCIA realizada."""
    __tablename__ = "payment_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    charge_id = Column(UUID(as_uuid=True), ForeignKey("monthly_charges.id"), nullable=False)
    
    amount_paid = Column(Float, nullable=False) # Cuánto pagó en este movimiento
    payment_date = Column(DateTime(timezone=True), server_default=func.now()) # Fecha exacta del registro
    method = Column(Enum(PaymentMethod), nullable=False)
    
    # Relaciones
    charge = relationship("MonthlyCharge", back_populates="transactions")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, nullable=False)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WaitingList(Base):
    __tablename__ = "waiting_list"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    box_type = Column(String, nullable=False)
    message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())