#!/usr/bin/env python3

import quart
import socket, select
import threading
import aiohttp
import asyncio
from prometheus_client import Gauge, generate_latest, REGISTRY
from datetime import datetime, timedelta
from dataclasses import dataclass

power = Gauge('mystrom_power', 'Power consumption', labelnames=['device'])
energy = Gauge('mystrom_energy', 'Energy consumption', labelnames=['device'])

devices= set()

timestamps: dict[str, datetime] = {}

        
class UdpListener:

    terminate = False

    def run(self):
        s = socket.socket(type=socket.SOCK_DGRAM)
        s.bind(('',7979))
        s.settimeout(1)

        while not self.terminate:
            try:
                data, addr = s.recvfrom(1024)
                ip, _ = addr
                devices.add(ip)
            except socket.timeout:
                pass
        print("Terminating")

@dataclass
class Measurement:
    device: str
    power: float
    avg_power: float
    interval: timedelta

    @property
    def energy(self) -> float:
        return self.avg_power * self.interval.total_seconds() / 3600



listener = UdpListener()
app = quart.Quart(__name__)

async def get_measurement(ip: str, session: aiohttp.ClientSession)-> Measurement|None:
    try:
        res = await session.get(f"http://{ip}/report", timeout=1)
    except aiohttp.ClientConnectorError:
        devices.remove(ip)
        return None

    data = await res.json()

    last = timestamps.get(ip, datetime.now())
    timestamps[ip] = datetime.now()
    delta = timestamps[ip] - last

    return Measurement(ip, data['power'], data['Ws'], delta)

async def collect_measurements():
    async with aiohttp.ClientSession() as session:
        while True:
            if len(devices) == 0:
                await asyncio.sleep(1)
                continue

            results = await asyncio.gather(*[get_measurement(ip, session) for ip in devices])

            for meas in results:
                if meas is None: 
                    continue
                power.labels(device=meas.device).set(meas.power)
                energy.labels(device=meas.device).inc(meas.energy)

            await asyncio.sleep(15)

@app.before_serving
async def start_background_thread():
    app.add_background_task(listener.run)
    app.add_background_task(collect_measurements)

@app.after_serving
async def stop_background_thread():
    listener.terminate = True
    for task in app.background_tasks:
        task.cancel()
    app.background_tasks.clear()

@app.route('/')
async def index():
    return str(devices)

@app.route('/metrics')
def metrics():
    return generate_latest(REGISTRY)
