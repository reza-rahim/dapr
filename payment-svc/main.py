import os
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dapr.clients import DaprClient

APP_ID = os.getenv("DAPR_APP_ID", "payment-svc")
STATE_STORE = os.getenv("STATE_STORE", "statestore")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title=APP_ID, lifespan=lifespan)


class ChargeRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    amount_usd: float = Field(..., gt=0)


def _payment_key(order_id: str) -> str:
    return f"payment:{order_id}"


def _save_json_state(key: str, value: dict[str, Any]) -> None:
    """
    Save as JSON string to avoid SDK type issues.
    """
    body = json.dumps(value)
    with DaprClient() as d:
        d.save_state(store_name=STATE_STORE, key=key, value=body)


@app.post("/charge")
def charge(req: ChargeRequest):
    if req.amount_usd <= 0:
        raise HTTPException(status_code=400, detail="amount_usd must be > 0")

    payment_record = {
        "charged": True,
        "order_id": req.order_id,
        "customer_id": req.customer_id,
        "amount_usd": req.amount_usd,
    }

    try:
        _save_json_state(_payment_key(req.order_id), payment_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to persist payment: {e}")

    return {"charged": True}
