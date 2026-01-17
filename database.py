from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, String, DateTime, func, select, BigInteger
import datetime

DATABASE_URL = "sqlite+aiosqlite:///bot.db"
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, nullable=True)
    join_date: Mapped[datetime.datetime] = mapped_column(DateTime, default=func.now())
    request_count: Mapped[int] = mapped_column(Integer, default=0)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def add_user(tg_id: int, username: str, full_name: str):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=tg_id, username=username, full_name=full_name)
            session.add(user)
        else:
            user.username = username
            user.full_name = full_name
        await session.commit()

async def increment_stats(tg_id: int):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if user:
            user.request_count += 1
            await session.commit()

async def get_global_stats():
    async with async_session() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        total_requests = await session.scalar(select(func.sum(User.request_count)))
        res = await session.execute(select(User).order_by(User.request_count.desc()).limit(5))
        return {"users": total_users, "requests": total_requests or 0, "top": res.scalars().all()}

async def get_all_users_ids():
    async with async_session() as session:
        return (await session.execute(select(User.telegram_id))).scalars().all()
