import os
import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dapr.clients import DaprClient
from dapr.ext.workflow import DaprWorkflowClient
import traceback

APP_ID = os.getenv("DAPR_APP_ID", "order-api")
STATE_STORE = os.getenv("STATE_STORE", "statestore")
WORKFLOW_NAME = os.getenv("WORKFLOW_NAME", "order_processing_workflow")
WORKFLOW_COMPONENT = os.getenv("WORKFLOW_COMPONENT", "dapr")
PUBSUB = os.getenv("PUBSUB", "pubsub")
TOPIC = os.getenv("TOPIC", "orders.created")


app = FastAPI(title=APP_ID)


class CreateOrderRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)
    amount_usd: float = Field(..., gt=0)


def _order_key(order_id: str) -> str:
    return f"order:{order_id}"


@app.post("/orders", status_code=201)
def create_order(req: CreateOrderRequest):
    order_id = str(uuid.uuid4())
    print(order_id)

    initial_state: dict[str, Any] = {
        "order_id": order_id,
        "status": "CREATED",
        "customer_id": req.customer_id,
        "sku": req.sku,
        "qty": req.qty,
        "amount_usd": req.amount_usd,
    }

    workflow_input: dict[str, Any] = {
        "order_id": order_id,
        "customer_id": req.customer_id,
        "sku": req.sku,
        "qty": req.qty,
        "amount_usd": req.amount_usd,
    }


    try:
        # Save initial state
        with DaprClient() as d:
           d.save_state(
              store_name=STATE_STORE,
              key=_order_key(order_id),
              value=json.dumps(initial_state),
              state_metadata={"contentType": "application/json"},
           )

           # Publish ONLY order_id
           d.publish_event(
              pubsub_name=PUBSUB,
              topic_name=TOPIC,
              data=json.dumps({"order_id": order_id}),
           )
    except Exception as e:
        print("!!! save_state exception:", e)
        traceback.print_exc()
        raise
    return {"order_id": order_id, "status": "CREATED"}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    try:
        with DaprClient() as d:
            resp = d.get_state(store_name=STATE_STORE, key=_order_key(order_id))
            if not resp.data:
                raise HTTPException(status_code=404, detail="order not found")
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read order: {e}")
