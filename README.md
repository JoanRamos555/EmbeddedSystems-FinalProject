# 🤖 MeCar — Robot Controlado Remotamente con Brazo

Sistema de control remoto en tiempo real para un robot móvil con brazo articulado, cámara y sensor de distancia, basado en **Raspberry Pi Pico 2W** y una interfaz web como dashboard de control.

---

## Descripción del Sistema

MeCar es un robot controlado vía red local que combina:

- **Locomoción**: carro de 4 ruedas con motor DC doble (L293D)
- **Brazo robótico**: 3 servos (base, brazo, codo)
- **Visión**: cámara OV7670 que transmite frames en tiempo real
- **Sensor de distancia**: HC-SR04 (ultrasonido)
- **Conectividad**: Wi-Fi sobre TCP/IP con protocolo PubSub JSON propio
- **Interfaz**: dashboard HTML con control en tiempo real, feed de cámara y gemelo virtual 3D (Three.js)

La arquitectura separa el tráfico de **control/sensores** del tráfico de **video** usando dos conexiones TCP independientes al mismo broker, evitando bloqueos de buffer por el gran volumen de datos de la cámara.

---

## Diagrama de Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                      RED LOCAL Wi-Fi                            │
│                                                                 │
│   ┌─────────────────────────┐       ┌────────────────────────┐ │
│   │   Raspberry Pi Pico 2W  │       │     Broker TCP/WS      │ │
│   │                         │       │   (PC / servidor)      │ │
│   │  ┌───────────────────┐  │  TCP  │                        │ │
│   │  │  Socket "ctrl"    │◄─┼──────►│  Puerto 5051 (TCP)     │ │
│   │  │  control/sensores │  │       │  Puerto 5052 (WS/HTTP) │ │
│   │  └───────────────────┘  │       │                        │ │
│   │                         │  TCP  │  Enruta mensajes JSON  │ │
│   │  ┌───────────────────┐  │◄─────►│  PUB/SUB entre        │ │
│   │  │  Socket "cam"     │  │       │  clientes TCP y WS     │ │
│   │  │  stream de video  │  │       │                        │ │
│   │  └───────────────────┘  │       └──────────┬─────────────┘ │
│   │                         │                  │ WebSocket      │
│   │  Hardware:              │                  │                │
│   │  • OV7670 (I2C+PIO)    │       ┌──────────▼─────────────┐ │
│   │  • HC-SR04 (GPIO 20/21)│       │   Dashboard Web        │ │
│   │  • L293D  (GPIO 22-28) │       │   MeCarPage.html       │ │
│   │  • Servo Base  (GP18)  │       │                        │ │
│   │  • Servo Brazo (GP10)  │       │  • Panel de conexión   │ │
│   │  • Servo Codo  (GP11)  │       │  • Control del carro   │ │
│   └─────────────────────────┘       │  • Control del brazo   │ │
│                                     │  • Feed cámara (canvas)│ │
│                                     │  • Sensor distancia    │ │
│                                     │  • Gemelo virtual 3D   │ │
│                                     │  • Log de mensajes     │ │
│                                     └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Flujo de datos

```
[OV7670] ──base64──► Socket "cam" ──PUB camera/frame──► Broker ──WS──► Canvas HTML

[HC-SR04] ──cm──► Socket "ctrl" ──PUB distance/cm──► Broker ──WS──► Barra distancia HTML

[Dashboard] ──click──► WebSocket ──PUB car/control──► Broker ──TCP──► Socket "ctrl" ──► L293D
[Dashboard] ──click──► WebSocket ──PUB arm/control──► Broker ──TCP──► Socket "ctrl" ──► Servos
```

---

## Estructura de Tópicos (PubSub)

Todos los tópicos siguen el esquema de prefijo:

```
UDFJC/emb1/robot<ID>/
```

El `<ID>` por defecto es `0`, configurable en el dashboard.

| Tópico completo | Dirección | Publicador | Suscriptor | Payload |
|---|---|---|---|---|
| `UDFJC/emb1/robot0/camera/frame` | Pico → Web | `CameraPublisherTask` | Dashboard (canvas) | `{ "w": int, "h": int, "frame": "<base64 RGB565>" }` |
| `UDFJC/emb1/robot0/distance/cm` | Pico → Web | `DistanceSensorTask` | Dashboard (barra) | `{ "distance": float }` |
| `UDFJC/emb1/robot0/car/control` | Web → Pico | Dashboard | `RemoteControlTask` | `{ "cmd": string }` |
| `UDFJC/emb1/robot0/arm/control` | Web → Pico | Dashboard | `RemoteControlTask` | `{ "cmd": string }` o `{ "cmd": "base_angle", "angle": int }` |

### Comandos disponibles

**`car/control`** — campo `cmd`:

| Comando | Acción |
|---|---|
| `forward` | Avanzar (mientras se mantiene presionado) |
| `backward` | Retroceder |
| `left` | Girar izquierda |
| `right` | Girar derecha |
| `tank_left90` | Giro de tanque −90° |
| `tank_right90` | Giro de tanque +90° |
| `stop` | Detener motores |

**`arm/control`** — campo `cmd`:

| Comando | Articulación | Rango |
|---|---|---|
| `base_left` / `base_right` | Base (rotación horizontal) | −90° … 90° |
| `brazo_extend` / `brazo_retract` | Hombro | −90° … 90° |
| `codo_extend` / `codo_retract` | Codo | 0° … 180° |
| `base_angle` + `angle: N` | Base (posición absoluta) | −90° … 90° |
| `brazo_angle` + `angle: N` | Hombro (posición absoluta) | −90° … 90° |
| `codo_angle` + `angle: N` | Codo (posición absoluta) | 0° … 180° |

### Formato de paquete PubSub

```json
// Publicar
{ "action": "PUB", "topic": "UDFJC/emb1/robot0/car/control", "data": { "cmd": "forward" } }

// Suscribir
{ "action": "SUB", "topic": "UDFJC/emb1/robot0/distance/cm" }
```

---

## Estructura del Proyecto

```
meCar/
├── MeCarRaspberry.py      # Firmware MicroPython — Raspberry Pi Pico 2W
├── MeCarPage.html         # Dashboard web (abrir en navegador)
├── ov7670_wrapper.py      # Driver de la cámara OV7670 (dependencia)
├── .env                   # Contraseña Wi-Fi (una sola línea, sin comillas)
└── README.md
```

---

## Instrucciones para Ejecutar el Proyecto

### Requisitos de hardware

| Componente | Descripción |
|---|---|
| Raspberry Pi Pico 2W | Microcontrolador principal |
| Cámara OV7670 | Conectada por I2C (SDA GP16, SCL GP17) + PIO |
| HC-SR04 | Trig GP20, Echo GP21 |
| L293D | IN1 GP22, IN2 GP26, IN3 GP27, IN4 GP28 |
| Servo base | GP18 |
| Servo brazo | GP10 |
| Servo codo | GP11 |
| PC / servidor | Corre el broker TCP+WebSocket |

---

### 1. Preparar el broker

El broker es un servidor que actúa como intermediario entre el Pico (TCP) y el navegador (WebSocket). Debe estar corriendo antes de encender el robot.

El broker debe:
- Escuchar conexiones TCP en el **puerto 5051** (para el Pico)
- Escuchar conexiones WebSocket en el **puerto 5052** (para el navegador, ruta `/ws`)
- Enrutar paquetes `PUB` a todos los suscriptores del mismo tópico

> Si no tienes un broker, puedes implementar uno simple en Python con `asyncio` o usar un puente MQTT↔WebSocket.

---

### 2. Configurar el firmware del Pico

**a) Crear el archivo `.env`** en la raíz del Pico con la contraseña de la red Wi-Fi:

```
MiContraseñaWifi
```

**b) Editar `MeCarRaspberry.py`** y actualizar la IP del broker y el SSID:

```python
# Línea ~859
self.wifi = WiFiManager("NombreDeRedWifi")

# Líneas ~863 y ~884
SocketClient("10.183.68.19", 5051, label="ctrl")
# host="10.183.68.19"  ← reemplazar con la IP del broker
```

**c) Copiar al Pico** (usando Thonny o `mpremote`):

```bash
# Con mpremote
mpremote cp MeCarRaspberry.py :MeCarRaspberry.py
mpremote cp ov7670_wrapper.py :ov7670_wrapper.py
mpremote cp .env :.env
```

**d) Ejecutar** (o renombrar a `main.py` para arranque automático):

```bash
mpremote run MeCarRaspberry.py
```

La consola mostrará:

```
📷 Configurando cámara...
📷 Inicializando cámara...
✅ Cámara OK
📡 Conectando WiFi...
✅ WiFi: 10.183.68.19
🔌 [ctrl] Conectando...
✅ [ctrl] Conectado
📡 SUB: UDFJC/emb1/robot0/car/control
📡 SUB: UDFJC/emb1/robot0/arm/control
🚀 Sistema iniciado
```

---

### 3. Abrir el Dashboard

1. Abre `MeCarPage.html` directamente en el navegador (no requiere servidor web).
2. En el panel **Conexión**, ingresa la IP del broker y el puerto `5052`.
3. Haz clic en **Conectar** — el estado cambiará a 🟢 Conectado.
4. El prefijo base predeterminado es `UDFJC/emb1` con Robot ID `0`. Ajústalo si usas otro ID.

---

### 4. Usar el Dashboard

| Panel | Función |
|---|---|
| **Cámara OV7670** | Muestra el feed en tiempo real (canvas rotado −90°) |
| **Sensor HC-SR04** | Distancia en cm con barra de progreso |
| **Control Carro** | Botones hold (avanzar/retroceder/girar) y pulso (giro tanque 90°) |
| **Brazo Robot** | Botones +10°/−10° y entrada de ángulo absoluto para cada articulación |
| **Gemelo Virtual** | Modelo 3D interactivo (Three.js) que espeja los ángulos del brazo real |
| **Mensajes** | Publicar/suscribir tópicos arbitrarios para depuración |
| **Log** | Registro filtrable de todos los mensajes recibidos |

---

### Tareas periódicas (Scheduler del Pico)

| Tarea | Período | Función |
|---|---|---|
| `CameraPublisherTask` | 1000 ms | Captura frame OV7670 y publica en `camera/frame` |
| `DistanceSensorTask` | 300 ms | Lee HC-SR04 y publica en `distance/cm` |
| `RemoteControlTask` | 50 ms | Procesa hasta 10 paquetes de control por ciclo |
| `WatchdogTask` | 30 000 ms | Verifica Wi-Fi y memoria libre |
| **GC** | 5 000 ms | `gc.collect()` periódico para evitar fragmentación |

---

### Notas de red y estabilidad

- La cámara usa un **socket TCP separado** del socket de control para evitar que el volumen de datos (~57 KB/frame en base64) sature el buffer y cause `ETIMEDOUT` en los comandos.
- El `WatchdogTask` reconecta Wi-Fi y socket automáticamente si se cae la conexión.
- Tras 5 fallos consecutivos de captura, se intenta re-inicializar el sensor OV7670.
- El reloj del Pico corre a **200 MHz** para garantizar rendimiento en la captura de imagen.
