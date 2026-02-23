# OrderFlow (Dapr + Diagrid Catalyst take-home)

OrderFlow is a small order-processing system demonstrating:
- Dapr Workflow (durable orchestration)
- Dapr Service Invocation (worker calls inventory + payment services)
- Dapr Pub/Sub (emit OrderCompleted events)
- Dapr State Management (persist order + stock + notifications)

## Architecture

- order-api -> starts workflow instance (order_processing_workflow)
- order-worker -> hosts workflow + activities
- inventory-svc <- invoked via Dapr service invocation
- payment-svc   <- invoked via Dapr service invocation

## Prereqs (local)
- Dapr CLI installed and initialized (`dapr init`)
- Python 3.9+

## Run locally

Terminal 1:
```
```

Terminal 2:
```
dapr run --app-id order-api --app-port 8001 --resources-path ./components --log-level warn  -- uvicorn workflow-worker.main:app --host 0.0.0.0 --port 8001

```

Terminal 3:
```
dapr run --app-id inventory-svc --app-port 8002 --resources-path ./components --log-level warn   -- uvicorn inventory-svc.main:app --host 0.0.0.0 --port 8002 
```

Terminal 4:
```
dapr run --app-id payment-svc --app-port 8003 --resources-path ./components --log-level warn   -- uvicorn payment-svc.main:app --host 0.0.0.0 --port 8003 
```

Create an order:
```
curl -X POST http://localhost:8000/orders \
  -H "content-type: application/json" \
  -d '{"customer_id":"c-123","sku":"sku-1","qty":2,"amount_usd":19.99}'
```
Check status:
```
curl http://localhost:8000/orders/<order_id>
```
## Demo ideas
- Kill `inventory-svc` mid-run to show workflow retry + recovery.
- Show Catalyst topology + workflow timeline + API visibility.
