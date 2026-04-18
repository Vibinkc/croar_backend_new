from app.core.database import Base


class SharedBase(Base):
    __abstract__ = True
