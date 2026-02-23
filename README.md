```
dapr run --app-id order-api --app-port 8000 --resources-path ./components --log-level warn  -- uvicorn order-api.main:app --host 0.0.0.0 --port 8000
dapr run --app-id order-api --app-port 8001 --resources-path ./components --log-level warn  -- uvicorn workflow-worker.main:app --host 0.0.0.0 --port 8001
dapr run --app-id inventory-svc --app-port 8002 --resources-path ./components --log-level warn   -- uvicorn inventory-svc.main:app --host 0.0.0.0 --port 8002 
dapr run --app-id payment-svc --app-port 8003 --resources-path ./components --log-level warn   -- uvicorn payment-svc.main:app --host 0.0.0.0 --port 8003 


curl -X POST http://localhost:8000/orders   -H "content-type: application/json"   -d '{"customer_id":"c-123","sku":"sku-1","qty":2,"amount_usd":19.99}' 

curl -X GET http://localhost:8000/orders/735afc0c-516c-4ed3-9378-39d345e0be4b
```
