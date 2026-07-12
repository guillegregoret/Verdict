/**
 * kuma-init — Provisioning headless de Uptime Kuma 2.x (one-shot, idempotente).
 *
 * Kuma no tiene API REST de administración: se administra por socket.io. Este
 * script corre en un contenedor one-shot con la MISMA imagen de Kuma (así
 * reutiliza el socket.io-client de /app/node_modules vía NODE_PATH) y:
 *
 *   1. Si la instancia está virgen → crea el admin (KUMA_ADMIN_USER/PASSWORD).
 *   2. Login.
 *   3. Crea la notificación de Telegram (si hay TELEGRAM_BOT_TOKEN) como default.
 *   4. Crea los monitores que falten (compara por nombre → idempotente):
 *      - push del heartbeat de la app (dead-man's switch, token de .env)
 *      - TCP a ib-gateway:4004 (API paper vía socat) y postgres:5432
 *      - HTTP a Grafana y, si el perfil analytics está activo, Metabase/Superset
 *      - HTTP a las APIs externas (Finnhub / Anthropic / Telegram): cualquier
 *        respuesta HTTP < 500 cuenta como "vivo" (sin auth devuelven 4xx).
 */
"use strict";

const { io } = require("socket.io-client");

const KUMA_URL = process.env.KUMA_URL || "http://uptime-kuma:3001";
const ADMIN_USER = process.env.KUMA_ADMIN_USER || "admin";
const ADMIN_PASSWORD = process.env.KUMA_ADMIN_PASSWORD;
const PUSH_TOKEN = process.env.KUMA_PUSH_TOKEN;
const TG_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const TG_CHAT = process.env.TELEGRAM_CHAT_ID || "";
const ANALYTICS = (process.env.COMPOSE_PROFILES || "")
  .split(",")
  .map((s) => s.trim())
  .includes("analytics");

const TIMEOUT_MS = 240_000; // si Kuma no responde en 4 min, fallar el init

if (!ADMIN_PASSWORD) {
  console.error("kuma-init: falta KUMA_ADMIN_PASSWORD en el entorno.");
  process.exit(1);
}
if (!PUSH_TOKEN) {
  console.error("kuma-init: falta KUMA_PUSH_TOKEN en el entorno.");
  process.exit(1);
}

// Defaults comunes que la UI manda al crear un monitor (validación del server).
const BASE = {
  interval: 60,
  retryInterval: 60,
  resendInterval: 0,
  maxretries: 2,
  upsideDown: false,
  expiryNotification: false,
  ignoreTls: false,
  accepted_statuscodes: ["200-299"],
  conditions: [],
  method: "GET",
  active: true,
};

function monitorsWanted(notificationIDList) {
  const mk = (extra) => ({ ...BASE, notificationIDList, ...extra });
  const wanted = [
    mk({
      type: "push",
      name: "App heartbeat (push)",
      pushToken: PUSH_TOKEN,
      interval: 180, // la app pinga cada tick (60s); 3 ticks sin señal = down
    }),
    mk({
      type: "port",
      name: "IB Gateway API (tcp 4004)",
      hostname: "ib-gateway",
      port: 4004,
    }),
    mk({
      type: "port",
      name: "Postgres (tcp 5432)",
      hostname: "postgres",
      port: 5432,
    }),
    mk({
      type: "http",
      name: "Grafana",
      url: "http://grafana:3000/api/health",
    }),
    // APIs externas: sin API key devuelven 401/403 — eso también prueba que
    // están vivas, por eso se acepta todo 2xx-4xx.
    mk({
      type: "http",
      name: "Finnhub API",
      url: "https://finnhub.io/api/v1/quote?symbol=AAPL",
      accepted_statuscodes: ["200-499"],
      interval: 300,
    }),
    mk({
      type: "http",
      name: "Anthropic API",
      url: "https://api.anthropic.com/v1/messages",
      accepted_statuscodes: ["200-499"],
      interval: 300,
    }),
    mk({
      type: "http",
      name: "Telegram API",
      url: "https://api.telegram.org",
      accepted_statuscodes: ["200-499"],
      interval: 300,
    }),
  ];
  if (ANALYTICS) {
    wanted.push(
      mk({
        type: "http",
        name: "Metabase",
        url: "http://metabase:3000/api/health",
        interval: 120,
      }),
      mk({
        type: "http",
        name: "Superset",
        url: "http://superset:8088/health",
        interval: 120,
      }),
    );
  }
  return wanted;
}

function emit(socket, event, ...args) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(
      () => reject(new Error(`timeout esperando ack de "${event}"`)),
      30_000,
    );
    socket.emit(event, ...args, (res) => {
      clearTimeout(t);
      resolve(res);
    });
  });
}

function waitEvent(socket, event, ms = 15_000) {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(null), ms);
    socket.once(event, (data) => {
      clearTimeout(t);
      resolve(data);
    });
  });
}

async function main() {
  console.log(`kuma-init: conectando a ${KUMA_URL} ...`);
  const socket = io(KUMA_URL, {
    transports: ["websocket"],
    reconnection: true,
    reconnectionDelay: 2000,
  });

  await new Promise((resolve, reject) => {
    const t = setTimeout(
      () => reject(new Error("timeout conectando a Uptime Kuma")),
      TIMEOUT_MS,
    );
    socket.on("connect", () => {
      clearTimeout(t);
      resolve();
    });
  });
  console.log("kuma-init: conectado.");

  // monitorList / notificationList llegan como eventos después del login;
  // registrarlos ANTES para no perderlos.
  let monitorList = {};
  let notificationList = [];
  socket.on("monitorList", (list) => (monitorList = list || {}));
  socket.on("notificationList", (list) => (notificationList = list || []));

  // 1. Setup del admin si la instancia está virgen.
  const needSetup = await emit(socket, "needSetup");
  if (needSetup === true) {
    console.log("kuma-init: instancia virgen → creando admin ...");
    const res = await emit(socket, "setup", ADMIN_USER, ADMIN_PASSWORD);
    if (!res || !res.ok) {
      throw new Error(`setup falló: ${res && res.msg}`);
    }
    console.log("kuma-init: admin creado.");
  }

  // 2. Login.
  const login = await emit(socket, "login", {
    username: ADMIN_USER,
    password: ADMIN_PASSWORD,
    token: "",
  });
  if (!login || !login.ok) {
    throw new Error(
      `login falló (${login && login.msg}). ¿KUMA_ADMIN_PASSWORD correcto?`,
    );
  }
  console.log("kuma-init: login OK.");

  // Dar tiempo a que lleguen las listas post-login.
  await waitEvent(socket, "monitorList", 10_000);

  // 3. Notificación Telegram (default + aplicar a monitores existentes).
  const notificationIDList = {};
  if (TG_TOKEN && TG_CHAT) {
    const existing = notificationList.find((n) => n.name === "Telegram (Verdict)");
    if (existing) {
      notificationIDList[existing.id] = true;
      console.log("kuma-init: notificación Telegram ya existe, se reutiliza.");
    } else {
      const res = await emit(
        socket,
        "addNotification",
        {
          name: "Telegram (Verdict)",
          type: "telegram",
          isDefault: true,
          applyExisting: true,
          telegramBotToken: TG_TOKEN,
          telegramChatID: TG_CHAT,
        },
        null,
      );
      if (res && res.ok) {
        notificationIDList[res.id] = true;
        console.log("kuma-init: notificación Telegram creada.");
      } else {
        console.warn(`kuma-init: no pude crear la notificación: ${res && res.msg}`);
      }
    }
  } else {
    console.log("kuma-init: sin TELEGRAM_BOT_TOKEN/CHAT_ID → monitores sin notificación.");
  }

  // 4. Monitores que falten (idempotente por nombre).
  const existingNames = new Set(
    Object.values(monitorList).map((m) => m.name),
  );
  let created = 0;
  for (const monitor of monitorsWanted(notificationIDList)) {
    if (existingNames.has(monitor.name)) {
      console.log(`kuma-init: monitor "${monitor.name}" ya existe, salto.`);
      continue;
    }
    const res = await emit(socket, "add", monitor);
    if (res && res.ok) {
      created += 1;
      console.log(`kuma-init: monitor "${monitor.name}" creado (id ${res.monitorID}).`);
    } else {
      throw new Error(`no pude crear "${monitor.name}": ${res && res.msg}`);
    }
  }

  console.log(`kuma-init: listo (${created} monitores nuevos).`);
  socket.close();
  process.exit(0);
}

main().catch((err) => {
  console.error(`kuma-init: ERROR — ${err.message}`);
  process.exit(1);
});
