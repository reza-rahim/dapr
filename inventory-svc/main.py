import os
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dapr.clients import DaprClient

APP_ID = os.getenv("DAPR_APP_ID", "inventory-svc")
STATE_STORE = os.getenv("STATE_STORE", "statestore")

# For demo purposes: start with 10 units per SKU unless set
DEFAULT_STOCK = int(os.getenv("DEFAULT_STOCK", "10"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # no workflow runtime here; just keep consistent style with worker/main.py
    yield


app = FastAPI(title=APP_ID, lifespan=lifespan)


class ReserveRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)


def _stock_key(sku: str) -> str:
    return f"stock:{sku}"


def _read_int_state(d: DaprClient, key: str) -> Optional[int]:
    resp = d.get_state(store_name=STATE_STORE, key=key)
    if not resp.data:
        return None
    try:
        # We store stock as a plain string/int
        return int(resp.data.decode("utf-8"))
    except Exception:
        # If something stored JSON, try parsing it
        try:
            obj = json.loads(resp.data.decode("utf-8"))
            if isinstance(obj, int):
                return obj
            if isinstance(obj, dict) and "stock" in obj:
                return int(obj["stock"])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"invalid stock state for key={key}")


def _write_int_state(d: DaprClient, key: str, value: int) -> None:
    # Save as string for compatibility with simple readers
    d.save_state(store_name=STATE_STORE, key=key, value=str(value))


@app.post("/reserve")
def reserve(req: ReserveRequest):
    # qty validation is already enforced by Pydantic (gt=0), keep safety anyway
    if req.qty <= 0:
        raise HTTPException(status_code=400, detail="qty must be > 0")

    key = _stock_key(req.sku)

    with DaprClient() as d:
        stock = _read_int_state(d, key)
        if stock is None:
            stock = DEFAULT_STOCK

        if stock < req.qty:
            raise HTTPException(status_code=409, detail=f"insufficient stock for {req.sku}")

        remaining = stock - req.qty
        _write_int_state(d, key, remaining)

    return {"reserved": True, "remaining": remaining}
