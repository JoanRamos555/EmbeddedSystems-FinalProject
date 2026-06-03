# =============================================================
# MICRO PYTHON PUBSUB CLIENT
# OV7670 + HC-SR04 + CARRO + BRAZO
# Raspberry Pi Pico 2W
# VERSION ESTABLE v2 - Socket separado para cámara + reconexión
# =============================================================

import network
import time
import json
import gc
import sys
import machine

import usocket as socket
import ubinascii

from machine import (
    Pin,
    I2C,
    PWM,
    time_pulse_us
)

from ov7670_wrapper import *

# =========================================================
# CLOCK
# =========================================================

machine.freq(200000000)

# =========================================================
# HC-SR04
# =========================================================

_HCSR04_TRIG = 20
_HCSR04_ECHO = 21

# =========================================================
# L293D
# =========================================================

_MOTOR_IN1 = 22
_MOTOR_IN2 = 26
_MOTOR_IN3 = 27
_MOTOR_IN4 = 28

# =========================================================
# SERVOS
# =========================================================

_SERVO_BASE  = 18
_SERVO_BRAZO = 10
_SERVO_CODO  = 11

# =========================================================
# OV7670
# =========================================================

mclk_pin_no     = 9
pclk_pin_no     = 8
data_pin_base   = 0
vsync_pin_no    = 13
href_pin_no     = 12
reset_pin_no    = 14
shutdown_pin_no = 15
sda_pin_no      = 16
scl_pin_no      = 17

print("📷 Configurando cámara...")

pwm = PWM(Pin(mclk_pin_no))

pwm.freq(12_000_000)

pwm.duty_u16(32768)

time.sleep(1)

print("📷 Inicializando cámara...")

i2c = I2C(
    0,
    freq=100000,
    scl=Pin(scl_pin_no),
    sda=Pin(sda_pin_no)
)

try:

    ov7670 = OV7670Wrapper(
        i2c_bus=i2c,
        mclk_pin_no=mclk_pin_no,
        pclk_pin_no=pclk_pin_no,
        data_pin_base=data_pin_base,
        vsync_pin_no=vsync_pin_no,
        href_pin_no=href_pin_no,
        reset_pin_no=reset_pin_no,
        shutdown_pin_no=shutdown_pin_no,
    )

    ov7670.wrapper_configure_rgb()

    ov7670.wrapper_configure_base()

    cam_width, cam_height = ov7670.wrapper_configure_size(
        OV7670_WRAPPER_SIZE_DIV4
    )

    ov7670.wrapper_configure_test_pattern(
        OV7670_WRAPPER_TEST_PATTERN_NONE
    )

    print("✅ Cámara OK")

except Exception as e:

    print("❌ Error cámara:", e)

    sys.exit(1)

# =========================================================
# WIFI
# =========================================================

class WiFiManager:

    def __init__(self,
                 ssid,
                 password_file=".env"):

        self.ssid = ssid

        with open(password_file) as f:
            self.password = f.read().strip()

        self.wlan = network.WLAN(
            network.STA_IF
        )

        self.wlan.active(True)

    def connect(self):

        print("📡 Conectando WiFi...")

        if not self.wlan.isconnected():

            self.wlan.connect(
                self.ssid,
                self.password
            )

            while not self.wlan.isconnected():

                time.sleep(0.5)

        print(
            "✅ WiFi:",
            self.wlan.ifconfig()[0]
        )

    def is_connected(self):
        return self.wlan.isconnected()

    def reconnect_if_needed(self):
        """Reconecta WiFi si se cayó. Llamar periódicamente."""
        if not self.wlan.isconnected():
            print("⚠️ WiFi caído, reconectando...")
            self.connect()

# =========================================================
# SOCKET
# FIX: Se agregó reconnect() real, detección de socket roto
#      en recv_line(), y reintento automático en send_json().
# =========================================================

class SocketClient:

    def __init__(self,
                 host,
                 port,
                 label="sock"):

        self.host  = host
        self.port  = port
        self.label = label   # para distinguir en los logs
        self.sock  = None

    def connect(self):

        print(f"🔌 [{self.label}] Conectando {self.host}:{self.port}...")

        addr = socket.getaddrinfo(
            self.host,
            self.port
        )[0][-1]

        s = socket.socket()
        s.connect(addr)
        s.settimeout(0.05)

        self.sock = s

        print(f"✅ [{self.label}] Conectado")

    def reconnect(self):
        """Cierra el socket actual y abre uno nuevo limpiamente."""
        print(f"🔄 [{self.label}] Reconectando...")

        try:
            if self.sock is not None:
                self.sock.close()
        except:
            pass

        self.sock = None

        time.sleep_ms(400)

        try:
            self.connect()
        except Exception as e:
            print(f"❌ [{self.label}] Reconexión falló:", e)

    def ensure(self):
        """Conecta si no hay socket activo."""
        if self.sock is None:
            self.connect()

    # --------------------------------------------------
    # ENVÍO
    # --------------------------------------------------

    def send(self, data):

        total = 0

        while total < len(data):

            sent = self.sock.send(data[total:])

            if sent == 0:
                raise OSError("Socket cerrado")

            total += sent

    def send_json(self, obj):
        """
        Serializa obj como JSON y lo envía.
        Si el socket está roto, reconecta y reintenta UNA vez.
        """
        payload = (json.dumps(obj) + "\n").encode()

        try:
            self.ensure()
            self.send(payload)

        except OSError as e:
            print(f"⚠️ [{self.label}] send_json error ({e}), reconectando...")
            self.reconnect()

            try:
                self.send(payload)
            except Exception as e2:
                print(f"❌ [{self.label}] Reintento fallido:", e2)

    # --------------------------------------------------
    # RECEPCIÓN
    # FIX: antes tragaba TODOS los errores con `except: return None`
    #      y nunca detectaba el socket roto → ahora reconecta.
    # --------------------------------------------------

    def recv_line(self):

        if self.sock is None:
            return None

        try:
            data = self.sock.readline()

            # readline() devuelve b'' cuando el servidor cerró la conexión
            if data == b'' or data is None:
                raise OSError("Servidor cerró la conexión")

            return json.loads(data)

        except OSError as e:
            # Error de red real: socket roto, timeout duro, etc.
            # ETIMEDOUT tiene errno 110; timeout suave (settimeout) lanza
            # OSError con errno 110 también en MicroPython → distinguimos
            # por el mensaje para no reconectar en cada poll vacío.
            msg = str(e)
            if "110" in msg or "ETIMEDOUT" in msg:
                # Timeout suave del poll → normal, no reconectar
                pass
            else:
                print(f"⚠️ [{self.label}] recv OSError ({e}), reconectando...")
                self.reconnect()

            return None

        except ValueError:
            # JSON malformado — ignorar paquete
            return None

        except Exception as e:
            print(f"⚠️ [{self.label}] recv error inesperado:", e)
            return None

# =========================================================
# PUBSUB
# =========================================================

class PubSubClient:

    def __init__(self,
                 socket_client,
                 prefix='UDFJC/emb1/robot0/'):

        self.sock   = socket_client
        self.prefix = prefix

    def publish(self, topic, data):

        pkt = {
            "action": "PUB",
            "topic":  self.prefix + topic,
            "data":   data
        }

        self.sock.send_json(pkt)

    def subscribe(self, topic):

        pkt = {
            "action": "SUB",
            "topic":  self.prefix + topic
        }

        self.sock.send_json(pkt)

        print("📡 SUB:", self.prefix + topic)

# =========================================================
# SCHEDULER
# FIX: gc.collect() cada 5 s en lugar de cada iteración
#      (en el Pico 2W llamarlo cada ms es muy costoso).
# =========================================================

class Task:

    def __init__(self,
                 scheduler,
                 period_ms):

        self.period   = period_ms
        self.next_run = time.ticks_ms()

        scheduler.add(self)

    def update(self):
        pass

class Scheduler:

    def __init__(self):

        self.tasks    = []
        self._gc_tick = time.ticks_ms()

    def add(self, task):
        self.tasks.append(task)

    def run(self):

        while True:

            now = time.ticks_ms()

            for task in self.tasks:

                if time.ticks_diff(now, task.next_run) >= 0:

                    task.update()

                    task.next_run = time.ticks_add(
                        now,
                        task.period
                    )

            # GC cada 5 segundos, no cada iteración
            if time.ticks_diff(now, self._gc_tick) >= 5000:
                gc.collect()
                self._gc_tick = now

            time.sleep_ms(1)

# =========================================================
# HC-SR04
# =========================================================

class HCSR04:

    def __init__(self,
                 trig_pin,
                 echo_pin):

        self.trig = Pin(trig_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.trig.low()

        time.sleep_ms(50)

    def distance_cm(self):

        self.trig.low()
        time.sleep_us(2)

        self.trig.high()
        time.sleep_us(10)

        self.trig.low()

        duration = time_pulse_us(self.echo, 1, 30000)

        if duration < 0:
            return None

        return round((duration / 2) / 29.1, 2)

# =========================================================
# SERVO
# =========================================================

class Servo:

    def __init__(self,
                 pin,
                 modo="normal"):

        self.pwm = PWM(Pin(pin))
        self.pwm.freq(50)
        self.modo = modo

    def escribir(self, angulo):

        if self.modo == "centrado":
            angulo = max(-90, min(90, angulo))
            angulo += 90
        else:
            angulo = max(0, min(180, angulo))

        duty = int(
            (500 + (angulo / 180) * 2000)
            / 20000 * 65535
        )

        self.pwm.duty_u16(duty)

# =========================================================
# BRAZO
# =========================================================

class RobotArm:

    def __init__(self):

        self.base  = Servo(_SERVO_BASE,  "centrado")
        self.brazo = Servo(_SERVO_BRAZO, "centrado")
        self.codo  = Servo(_SERVO_CODO,  "normal")

        self.base_angle  = 0
        self.brazo_angle = 0
        self.codo_angle  = 90

        self.update_all()

    def update_all(self):
        self.base.escribir(self.base_angle)
        self.brazo.escribir(self.brazo_angle)
        self.codo.escribir(self.codo_angle)

    # BASE

    def base_left(self):
        self.base_angle += 10
        if self.base_angle > 90:
            self.base_angle = 90
        print("↪ BASE:", self.base_angle)
        self.base.escribir(self.base_angle)
        time.sleep_ms(80)

    def base_right(self):
        self.base_angle -= 10
        if self.base_angle < -90:
            self.base_angle = -90
        print("↩ BASE:", self.base_angle)
        self.base.escribir(self.base_angle)
        time.sleep_ms(80)

    # BRAZO

    def brazo_extend(self):
        self.brazo_angle += 10
        if self.brazo_angle > 90:
            self.brazo_angle = 90
        print("⬆ BRAZO:", self.brazo_angle)
        self.brazo.escribir(self.brazo_angle)
        time.sleep_ms(80)

    def brazo_retract(self):
        self.brazo_angle -= 10
        if self.brazo_angle < -90:
            self.brazo_angle = -90
        print("⬇ BRAZO:", self.brazo_angle)
        self.brazo.escribir(self.brazo_angle)
        time.sleep_ms(80)

    # CODO

    def codo_extend(self):
        self.codo_angle += 10
        if self.codo_angle > 180:
            self.codo_angle = 180
        print("🦾 CODO:", self.codo_angle)
        self.codo.escribir(self.codo_angle)
        time.sleep_ms(80)

    def codo_retract(self):
        self.codo_angle -= 10
        if self.codo_angle < 0:
            self.codo_angle = 0
        print("🦾 CODO:", self.codo_angle)
        self.codo.escribir(self.codo_angle)
        time.sleep_ms(80)

# =========================================================
# CARRO
# =========================================================

class CarController:

    def __init__(self):

        self.in1 = Pin(_MOTOR_IN1, Pin.OUT)
        self.in2 = Pin(_MOTOR_IN2, Pin.OUT)
        self.in3 = Pin(_MOTOR_IN3, Pin.OUT)
        self.in4 = Pin(_MOTOR_IN4, Pin.OUT)

        self.stop()

    def stop(self):
        self.in1.low(); self.in2.low()
        self.in3.low(); self.in4.low()

    def forward(self):
        self.in1.high(); self.in2.low()
        self.in3.high(); self.in4.low()

    def backward(self):
        self.in1.low(); self.in2.high()
        self.in3.low(); self.in4.high()

    def right(self):
        self.in1.high(); self.in2.low()
        self.in3.low();  self.in4.high()

    def left(self):
        self.in1.low();  self.in2.high()
        self.in3.high(); self.in4.low()

    def move_forward_meter(self):
        self.forward()
        time.sleep(1.8)
        self.stop()

    def move_backward_meter(self):
        self.backward()
        time.sleep(1.8)
        self.stop()

    def turn_right_90(self):
        self.right()
        time.sleep(0.55)
        self.stop()

    def turn_left_90(self):
        self.left()
        time.sleep(0.55)
        self.stop()

    def turn_right_45(self):
        self.right()
        time.sleep(0.28)
        self.stop()

    def turn_left_45(self):
        self.left()
        time.sleep(0.28)
        self.stop()

# =========================================================
# CAMERA TASK
#
# FIX PRINCIPAL: la cámara ya NO comparte el socket con los
# comandos. Tiene su propio SocketClient dedicado.
# Así el stream de video (~57 KB/frame en base64) no satura
# el buffer del socket de control → desaparece el ETIMEDOUT.
#
# Cambios adicionales:
#   • reconnect() explícito si el socket de cámara cae
#   • period_ms sube a 1000 ms (más seguro para el Pico 2W)
#   • gc.collect() solo antes de capture(), no en cada poll
# =========================================================

class CameraPublisherTask(Task):

    def __init__(self,
                 scheduler,
                 host,
                 port,
                 camera,
                 width,
                 height,
                 period_ms=1000):   # ← 1 s es más conservador

        super().__init__(scheduler, period_ms)

        self.camera = camera
        self.WIDTH  = width
        self.HEIGHT = height

        self.buf = bytearray(width * height * 2)

        self.error_count = 0

        # Socket PROPIO para la cámara — no comparte con control
        self._sock   = SocketClient(host, port, label="cam")
        self._pubsub = PubSubClient(self._sock)

    def update(self):

        try:

            # Asegurar conexión antes de capturar
            self._sock.ensure()

            # Liberar memoria justo antes del frame grande
            gc.collect()

            self.camera.capture(self.buf)

            frame_b64 = ubinascii.b2a_base64(
                self.buf
            ).decode().strip()

            self._pubsub.publish(
                "camera/frame",
                {
                    "w":     self.WIDTH,
                    "h":     self.HEIGHT,
                    "frame": frame_b64
                }
            )

            self.error_count = 0

        except Exception as e:

            self.error_count += 1

            print("❌ Cámara:", e)

            # Cerrar socket de cámara para forzar reconexión
            # en el próximo ciclo (lo hace ensure() al ver sock=None)
            try:
                self._sock.sock.close()
            except:
                pass
            self._sock.sock = None

            # Tras 5 fallos consecutivos intentar re-init del sensor
            if self.error_count >= 5:
                try:
                    print("🔄 Reiniciando sensor cámara...")
                    self.camera.wrapper_configure_base()
                    self.error_count = 0
                except Exception as e2:
                    print("❌ Reinicio cámara:", e2)

            # Espera larga para no saturar el bus
            time.sleep_ms(800)

# =========================================================
# DISTANCIA
# =========================================================

class DistanceSensorTask(Task):

    def __init__(self,
                 scheduler,
                 pubsub,
                 sensor,
                 period_ms=300):

        super().__init__(scheduler, period_ms)

        self.pubsub = pubsub
        self.sensor = sensor

    def update(self):

        try:

            dist = self.sensor.distance_cm()

            if dist is not None:

                self.pubsub.publish(
                    "distance/cm",
                    {"distance": dist}
                )

        except:
            pass

# =========================================================
# REMOTE CONTROL
# =========================================================

class RemoteControlTask(Task):

    def __init__(self,
                 scheduler,
                 socket_client,
                 car,
                 arm,
                 period_ms=50):

        super().__init__(scheduler, period_ms)

        self.sock = socket_client
        self.car  = car
        self.arm  = arm

    def update(self):

        try:

            # Leer TODOS los paquetes disponibles en este ciclo
            # (antes solo leía 1 → el stop llegaba justo después
            #  y cancelaba left/right antes de que se notara)
            for _ in range(10):

                pkt = self.sock.recv_line()

                if pkt is None:
                    break

                if pkt.get("action") != "PUB":
                    continue

                topic = pkt.get("topic", "")

                # CARRO

                if topic.endswith("car/control"):

                    cmd = pkt["data"].get("cmd", "")

                    print("🚗 CMD:", cmd)

                    if   cmd == "forward":      self.car.forward()
                    elif cmd == "backward":     self.car.backward()
                    elif cmd == "left":         self.car.left()
                    elif cmd == "right":        self.car.right()
                    elif cmd == "tank_left90":  self.car.turn_left_90()
                    elif cmd == "tank_right90": self.car.turn_right_90()
                    elif cmd == "stop":         self.car.stop()

                # BRAZO

                elif topic.endswith("arm/control"):

                    cmd = pkt["data"].get("cmd", "")

                    if   cmd == "base_left":     self.arm.base_left()
                    elif cmd == "base_right":    self.arm.base_right()
                    elif cmd == "brazo_extend":  self.arm.brazo_extend()
                    elif cmd == "brazo_retract": self.arm.brazo_retract()
                    elif cmd == "codo_extend":   self.arm.codo_extend()
                    elif cmd == "codo_retract":  self.arm.codo_retract()

                    elif cmd == "base_angle":
                        angle = pkt["data"].get("angle", 0)
                        self.arm.base_angle = max(-90, min(90, angle))
                        self.arm.base.escribir(self.arm.base_angle)
                        print("🎯 BASE →", self.arm.base_angle)

                    elif cmd == "brazo_angle":
                        angle = pkt["data"].get("angle", 0)
                        self.arm.brazo_angle = max(-90, min(90, angle))
                        self.arm.brazo.escribir(self.arm.brazo_angle)
                        print("🎯 BRAZO →", self.arm.brazo_angle)

                    elif cmd == "codo_angle":
                        angle = pkt["data"].get("angle", 0)
                        self.arm.codo_angle = max(0, min(180, angle))
                        self.arm.codo.escribir(self.arm.codo_angle)
                        print("🎯 CODO →", self.arm.codo_angle)

        except Exception as e:
            print("❌ Remote:", e)

# =========================================================
# WATCHDOG TASK
# Tarea nueva: verifica WiFi y socket de control cada 30 s.
# =========================================================

class WatchdogTask(Task):

    def __init__(self,
                 scheduler,
                 wifi,
                 socket_client,
                 pubsub,
                 period_ms=30000):

        super().__init__(scheduler, period_ms)

        self.wifi   = wifi
        self.sock   = socket_client
        self.pubsub = pubsub

    def update(self):

        # 1. Reconectar WiFi si se cayó
        self.wifi.reconnect_if_needed()

        # 2. Re-suscribir si el socket de control se reconectó
        #    (si sock es None significa que se cayó y reconnect ya corrió)
        if self.sock.sock is not None:
            try:
                free = gc.mem_free()
                print(f"💓 Watchdog OK | mem_free={free}")
            except:
                pass

# =========================================================
# MAIN
# =========================================================

class MainApp:

    def __init__(self):

        self.wifi = WiFiManager("Joan")

        # Socket de CONTROL (comandos + sensores) — tráfico pequeño
        self.socket = SocketClient(
            "10.183.68.19",
            5051,
            label="ctrl"
        )

        self.scheduler = Scheduler()

        self.pubsub = PubSubClient(self.socket)

        self.distance_sensor = HCSR04(
            trig_pin=_HCSR04_TRIG,
            echo_pin=_HCSR04_ECHO
        )

        self.car = CarController()
        self.arm = RobotArm()

        # Cámara con su PROPIO socket — no comparte con control
        self.camera_task = CameraPublisherTask(
            scheduler=self.scheduler,
            host="10.183.68.19",  # mismo broker, conexión independiente
            port=5051,
            camera=ov7670,
            width=cam_width,
            height=cam_height,
            period_ms=1000        # 1 frame/s — seguro para el Pico 2W
        )

        self.distance_task = DistanceSensorTask(
            scheduler=self.scheduler,
            pubsub=self.pubsub,
            sensor=self.distance_sensor
        )

        self.remote_control = RemoteControlTask(
            scheduler=self.scheduler,
            socket_client=self.socket,
            car=self.car,
            arm=self.arm
        )

        # Watchdog: monitorea WiFi y memoria
        self.watchdog = WatchdogTask(
            scheduler=self.scheduler,
            wifi=self.wifi,
            socket_client=self.socket,
            pubsub=self.pubsub
        )

    def run(self):

        self.wifi.connect()

        self.socket.connect()

        self.pubsub.subscribe("car/control")
        self.pubsub.subscribe("arm/control")

        print("🚀 Sistema iniciado")

        self.scheduler.run()

# =========================================================
# START
# =========================================================

app = MainApp()

app.run()
    