import os
import json
from datetime import timedelta
from typing import Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Response
from dapr.clients import DaprClient
from dapr.ext.workflow import (
    WorkflowRuntime,
    DaprWorkflowContext,
    WorkflowActivityContext,
    DaprWorkflowClient,
    RetryPolicy,
)

APP_ID = os.getenv("DAPR_APP_ID", "workflow-worker")

STATE_STORE = os.getenv("STATE_STORE", "statestore")
PUBSUB = os.getenv("PUBSUB", "pubsub")

# API publishes {"order_id": "..."} to this topic
IN_TOPIC = os.getenv("IN_TOPIC", "orders.created")
IN_ROUTE = os.getenv("IN_ROUTE", "orders-created")

# Worker publishes completion/failure events to this topic (optional)
OUT_TOPIC = os.getenv("OUT_TOPIC", "orders.events")

INVENTORY_APP_ID = os.getenv("INVENTORY_APP_ID", "inventory-svc")
PAYMENT_APP_ID = os.getenv("PAYMENT_APP_ID", "payment-svc")

WORKFLOW_NAME = os.getenv("WORKFLOW_NAME", "order_processing_workflow")

print(
    f"[CONFIG] APP_ID={APP_ID} STATE_STORE={STATE_STORE} PUBSUB={PUBSUB} "
    f"IN_TOPIC={IN_TOPIC} IN_ROUTE=/{IN_ROUTE} OUT_TOPIC={OUT_TOPIC}"
)

wfr = WorkflowRuntime()


# -----------------------------
# FastAPI lifespan (no deprecated on_event)
# -----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    wfr.start()
    try:
        yield
    finally:
        wfr.shutdown()


app = FastAPI(title=APP_ID, lifespan=lifespan)


# -----------------------------
# State helpers (FIXED: always serialize to JSON string and read from resp.data)
# -----------------------------
def _order_key(order_id: str) -> str:
    return f"order:{order_id}"


def _get_state_json(store: str, key: str) -> dict[str, Any]:
    with DaprClient() as d:
        resp = d.get_state(store_name=store, key=key)

        if not resp.data:
            return {}

        try:
            return json.loads(resp.data.decode("utf-8"))
        except Exception as e:
            print(f"[STATE READ ERROR] store={store} key={key} raw={resp.data[:200]!r} err={e}")
            return {}


def _save_state_json(store: str, key: str, value: dict[str, Any]) -> None:
    # Ensure wire format is deterministic and Dapr SDK receives str/bytes
    body = json.dumps(value)

    with DaprClient() as d:
        d.save_state(store_name=store, key=key, value=body)

        # Debug verification read (helps prove save happened)
        verify = d.get_state(store_name=store, key=key)
        snippet = (verify.data or b"")[:120]
        print(f"[STATE SAVED] store={store} key={key} bytes={len(verify.data or b'')} snippet={snippet!r}")


def set_order_status(order_id: str, status: str, extra: Optional[dict[str, Any]] = None) -> None:
    extra = extra or {}
    state = _get_state_json(STATE_STORE, _order_key(order_id))
    state["order_id"] = order_id
    state["status"] = status
    state.update(extra)

    print(f"[SET STATUS] order_id={order_id} status={status}")
    _save_state_json(STATE_STORE, _order_key(order_id), state)


def publish_event(event: dict[str, Any]) -> None:
    """
    IMPORTANT: Dapr Python SDK expects data as str/bytes, not dict.
    """
    with DaprClient() as d:
        d.publish_event(
            pubsub_name=PUBSUB,
            topic_name=OUT_TOPIC,
            data=json.dumps(event),
        )


# -----------------------------
# Activities
# -----------------------------
@wfr.activity(name="set_status_activity")
def set_status_activity(ctx: WorkflowActivityContext, inp: dict[str, Any]) -> None:
    set_order_status(inp["order_id"], inp["status"], inp.get("extra"))


@wfr.activity(name="publish_event_activity")
def publish_event_activity(ctx: WorkflowActivityContext, inp: dict[str, Any]) -> None:
    publish_event(inp)


@wfr.activity(name="reserve_inventory")
def reserve_inventory(ctx: WorkflowActivityContext, inp: dict[str, Any]) -> dict[str, Any]:
    """
    IMPORTANT: invoke_method data must be str/bytes.
    """
    payload = {"order_id": inp["order_id"], "sku": inp.get("sku"), "qty": inp.get("qty")}

    with DaprClient() as d:
        resp = d.invoke_method(
            app_id=INVENTORY_APP_ID,
            method_name="reserve",
            data=json.dumps(payload),
            http_verb="POST",
        )

    return json.loads(resp.data.decode("utf-8")) if resp.data else {}


@wfr.activity(name="charge_payment")
def charge_payment(ctx: WorkflowActivityContext, inp: dict[str, Any]) -> dict[str, Any]:
    """
    IMPORTANT: invoke_method data must be str/bytes.
    """
    payload = {
        "order_id": inp["order_id"],
        "customer_id": inp.get("customer_id"),
        "amount_usd": inp.get("amount_usd"),
    }

    with DaprClient() as d:
        resp = d.invoke_method(
            app_id=PAYMENT_APP_ID,
            method_name="charge",
            data=json.dumps(payload),
            http_verb="POST",
        )

    return json.loads(resp.data.decode("utf-8")) if resp.data else {}


# -----------------------------
# Orchestrator
# -----------------------------
@wfr.workflow(name=WORKFLOW_NAME)
def order_processing_workflow(ctx: DaprWorkflowContext, order: dict[str, Any]):
    """
    Input is the FULL order dict loaded from state store by the subscriber handler.
    """
    order_id = order["order_id"]

    retry = RetryPolicy(first_retry_interval=timedelta(seconds=2), max_number_of_attempts=5)

    try:
        # PROCESSING
        yield ctx.call_activity(set_status_activity, input={"order_id": order_id, "status": "PROCESSING"})

        # RESERVE
        reserve = yield ctx.call_activity(reserve_inventory, input=order, retry_policy=retry)
        yield ctx.call_activity(set_status_activity, input={"order_id": order_id, "status": "INVENTORY_RESERVED", "extra": reserve})

        # PAYMENT
        payment = yield ctx.call_activity(charge_payment, input=order, retry_policy=retry)
        yield ctx.call_activity(set_status_activity, input={"order_id": order_id, "status": "PAID", "extra": payment})

        # COMPLETED
        yield ctx.call_activity(set_status_activity, input={"order_id": order_id, "status": "COMPLETED"})
        return {"order_id": order_id, "result": "COMPLETED"}

    except Exception as e:
        print(f"[WORKFLOW FAILED] order_id={order_id} err={repr(e)}")
        try:
            yield ctx.call_activity(
                set_status_activity,
                input={"order_id": order_id, "status": "FAILED", "extra": {"error": str(e)}},
            )
        except Exception:
            pass
        raise


# -----------------------------
# Dapr pub/sub subscription endpoints
# -----------------------------
@app.get("/dapr/subscribe")
def dapr_subscribe():
    return [{"pubsubname": PUBSUB, "topic": IN_TOPIC, "route": IN_ROUTE}]


@app.post(f"/{IN_ROUTE}")
async def on_order_created(request: Request):
    """
    Receives ONLY order_id (possibly wrapped / CloudEvents):
      {"data": {"order_id": "..."}}
      {"order_id": "..."}
      CloudEvents: {"specversion":"1.0", ... , "data":{"order_id":"..."}}

    Loads full order from state store and starts workflow instance.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    # Robust envelope handling
    data: Any = payload
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            pass

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid message payload")

    order_id = data.get("order_id") or (payload.get("order_id") if isinstance(payload, dict) else None)
    if not order_id:
        raise HTTPException(status_code=400, detail="missing order_id")

    # Load order state
    order = _get_state_json(STATE_STORE, _order_key(order_id))
    if not order:
        # During debugging, return 500 to retry instead of acking forever
        raise HTTPException(status_code=500, detail=f"order not found in state store: {order_id}")

    # Start workflow locally
    wf = DaprWorkflowClient()
    try:
        # Optional idempotency: if instance exists, just ack
        try:
            existing = wf.get_workflow_state(order_id)
            if existing is not None:
                return Response(status_code=204)
        except Exception:
            pass

        wf.schedule_new_workflow(
            workflow=order_processing_workflow,  # function object
            instance_id=order_id,
            input=order,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to start workflow: {e}")

    return Response(status_code=204)


# Debug endpoint to confirm state is really there (uses fixed reader)
@app.get("/debug/orders/{order_id}")
def debug_get_order(order_id: str):
    return _get_state_json(STATE_STORE, _order_key(order_id))


@app.get("/healthz")
def healthz():
    return {"ok": True, "app_id": APP_ID}
