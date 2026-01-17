from sqlalchemy import Column, String, Integer, Float, Date, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from database import Base


class ClientStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEBTOR = "DEBTOR"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"


class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False, index=True)
    box_number = Column(Integer, nullable=False)
    status = Column(Enum(ClientStatus), default=ClientStatus.ACTIVE, nullable=False)

    payments = relationship("Payment", back_populates="client")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    amount = Column(Float, nullable=False)
    month_period = Column(Date, nullable=False)
    status = Column(Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    method = Column(Enum(PaymentMethod), nullable=True)

    client = relationship("Client", back_populates="payments")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True, nullable=False)
    value = Column(String, nullable=False)
