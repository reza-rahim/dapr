import os
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dapr.clients import DaprClient

APP_ID = os.getenv("DAPR_APP_ID", "order-api")
STATE_STORE = "statestore"
PUBSUB = os.getenv("PUBSUB", "pubsub")
TOPIC = os.getenv("TOPIC", "orders.created")

app = FastAPI(title="order-api")


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

    initial_state: dict[str, Any] = {
        "order_id": order_id,
        "status": "CREATED",
        "customer_id": req.customer_id,
        "sku": req.sku,
        "qty": req.qty,
        "amount_usd": req.amount_usd,
    }

    try:
        with DaprClient() as d:
            # Save full order state
            d.save_state(
                store_name=STATE_STORE,
                key=_order_key(order_id),
                value=initial_state,
            )

            # Publish ONLY order_id
            d.publish_event(
                pubsub_name=PUBSUB,
                topic_name=TOPIC,
                data=json.dumps({"order_id": order_id}),
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create order: {e}")

    return {"order_id": order_id, "status": "CREATED"}       

@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with DaprClient() as d:
        resp = d.get_state(store_name=STATE_STORE, key=f"order:{order_id}")
        if not resp.data:
            raise HTTPException(status_code=404, detail="order not found")
        return resp.json()
