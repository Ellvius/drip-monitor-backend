import time
import pigpio
import asyncio
from fastapi import FastAPI, WebSocket

# ---------------------------
# pigpio Setup
# ---------------------------
IR_PIN = 18   # GPIO pin where reflective IR sensor is connected
pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpio daemon. Start with: sudo systemctl start pigpiod")

pi.set_mode(IR_PIN, pigpio.INPUT)
pi.set_pull_up_down(IR_PIN, pigpio.PUD_UP)  # avoid floating

# ---------------------------
# Variables
# ---------------------------
drop_count = 0
last_drop_time = time.time()
drip_rate = 0   # drops per minute
alert_status = None  # "BLOCKED" | "STOPPED" | None
clients = []  # connected WebSocket clients

# ---------------------------
# Drop detection callback
# ---------------------------
def drop_detected(gpio, level, tick):
    """
    For reflective IR: a LOW (0) means reflection detected.
    We treat that as a drop event.
    """
    global drop_count, last_drop_time, drip_rate
    if level == 0:  # reflection detected = drop
        drop_count += 1
        now = time.time()
        elapsed = now - last_drop_time
        if elapsed > 0:
            drip_rate = 60 / elapsed  # drops/min
        last_drop_time = now

# Attach interrupt using pigpio
cb = pi.callback(IR_PIN, pigpio.FALLING_EDGE, drop_detected)

# ---------------------------
# FastAPI Setup
# ---------------------------
app = FastAPI()

@app.get("/drip-rate")
async def get_drip_rate():
    return {"message": format_response()}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            await websocket.send_text(format_response())
            await asyncio.sleep(2)  # send every 2s
    except Exception:
        if websocket in clients:
            clients.remove(websocket)

# ---------------------------
# Monitoring task (async)
# ---------------------------
def format_response():
    global alert_status, drip_rate
    drip_rate = drip_rate % 50
    if alert_status == "BLOCKED":
        return "ALERT: Drip too fast"
    elif alert_status == "STOPPED":
        return "ALERT: Drip stopped!"
    else:
        return f"Drip rate: {int(drip_rate)} drops/min"

async def monitor_loop():
    global alert_status
    while True:
        now = time.time()

        # --- Blocked alert: continuous reflection detected ---
        if pi.read(IR_PIN) == 0:  # reflection stays
            start_reflecting = now
            while pi.read(IR_PIN) == 0:
                await asyncio.sleep(0.1)
                if time.time() - start_reflecting > 3:  # reflecting > 3s
                    alert_status = "BLOCKED"
                    break

        # --- Stopped alert: no reflection detected for > 5s ---
        elif now - last_drop_time > 5:
            alert_status = "STOPPED"

        else:
            alert_status = None

        # Broadcast to WebSocket clients
        disconnected = []
        for ws in clients:
            try:
                await ws.send_text(format_response())
            except:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in clients:
                clients.remove(ws)

        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_loop())

@app.on_event("shutdown")
async def shutdown_event():
    cb.cancel()   # cancel the pigpio callback
    pi.stop()     # release pigpio connection


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
