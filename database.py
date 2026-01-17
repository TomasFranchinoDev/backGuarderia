import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# 1. Cargar variables de entorno (útil para local, en prod Railway las inyecta directo)
load_dotenv()

# 2. Obtener la URL de la base de datos
# En producción, Railway te da "DATABASE_URL" automáticamente.
# Si no existe, usamos un valor vacío para que falle controladamente después.
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ Error Crítico: No se encontró la variable DATABASE_URL.")

# 3. Corrección de protocolo para SQLAlchemy (Fix postgres:// -> postgresql://)
# Esto es vital porque algunos proveedores devuelven el protocolo antiguo.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 4. Crear el motor con configuración de Pooling (Vital para Producción)
engine = create_engine(
    DATABASE_URL,
    # pool_pre_ping: Verifica que la conexión esté viva antes de usarla (evita errores de desconexión)
    pool_pre_ping=True,
    # pool_recycle: Recicla conexiones viejas cada 30 min
    pool_recycle=1800,
    # pool_size: Mantener 10 conexiones base abiertas
    pool_size=10,
    # max_overflow: Permitir picos de hasta 20 conexiones extra
    max_overflow=20
)

# 5. Configurar la sesión y la base declarativa
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 6. Dependencia para obtener la DB en los endpoints
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 7. Inicialización de tablas (útil para proyectos simples sin migraciones como Alembic)
def init_db():
    Base.metadata.create_all(bind=engine)