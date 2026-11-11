/* Gemini Snake: un mini gioco per ingannare l'attesa mentre Gemini lavora.
 *
 * Il serpente è Gemini, il cibo sono le pagine PDF da "ingerire". Se perdi mentre il lavoro è
 * ancora in corso, il gioco riparte da solo. Quando il lavoro finisce, il serpente produce gli
 * indici (index.csv), esplode e passa ai risultati.
 *
 * Uso:
 *   const game = GeminiSnake.mount(container);
 *   game.setProgress(done, total);          // avanzamento vero del lavoro
 *   game.finish(() => vaiAiRisultati());    // animazione finale, poi callback
 *   game.destroy();
 */
(function () {
  "use strict";

  const COLS = 22, ROWS = 15;
  const COLORS = ["#4285F4", "#5B73E8", "#7B68D9", "#9B72CB", "#B96DB5", "#D96570"];
  const BITES = ["+1 pagina letta", "tabella digerita", "token gustosi", "codice FIN assorbito",
                 "legenda capita", "OCR? non serve", "contesto +1", "gnam, un primer", "PDF ingerito"];
  const RECORD_KEY = "ragpdf:snake-record";

  function readRecord() { try { return +localStorage.getItem(RECORD_KEY) || 0; } catch { return 0; } }
  function saveRecord(v) { try { localStorage.setItem(RECORD_KEY, String(v)); } catch { /* ignorato */ } }

  function mount(container, opts = {}) {
    container.innerHTML = `
      <div class="gs-wrap">
        <div class="gs-hud">
          <span class="gs-title">✦ Gemini Snake</span>
          <span class="gs-score">Pagine ingerite: <b data-score>0</b></span>
          <span class="gs-record">Record: <b data-record>0</b></span>
        </div>
        <div class="gs-progress" title="Avanzamento vero di Gemini"><div data-bar></div><span data-progress>Gemini al lavoro…</span></div>
        <div class="gs-stage">
          <canvas></canvas>
          <div class="gs-overlay" data-overlay></div>
        </div>
        <div class="gs-help">Frecce o WASD per muoverti · Spazio pausa · su telefono scorri il dito</div>
      </div>`;
    injectStyle();

    const canvas = container.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    const overlay = container.querySelector("[data-overlay]");
    const scoreEl = container.querySelector("[data-score]");
    const recordEl = container.querySelector("[data-record]");
    const barEl = container.querySelector("[data-bar]");
    const progressEl = container.querySelector("[data-progress]");

    let cell = 20, dpr = 1;
    let snake, dir, queue, food, score, state, paused, tickMs, lastTick, frame, started;
    let particles = [], floats = [], poops = [], flash = 0, finishAt = 0, onFinished = null, fallback = null;
    let record = readRecord();
    recordEl.textContent = record;

    function resize() {
      const width = Math.min((container.clientWidth || 540) - 20, 560);
      cell = Math.max(12, Math.floor(width / COLS));
      dpr = window.devicePixelRatio || 1;
      canvas.style.width = `${cell * COLS}px`;
      canvas.style.height = `${cell * ROWS}px`;
      canvas.width = cell * COLS * dpr;
      canvas.height = cell * ROWS * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function reset() {
      const y = Math.floor(ROWS / 2);
      snake = [{ x: 6, y }, { x: 5, y }, { x: 4, y }, { x: 3, y }];
      dir = { x: 1, y: 0 };
      queue = [];
      score = 0;
      tickMs = 150;
      scoreEl.textContent = "0";
      placeFood();
    }

    function placeFood() {
      let p;
      do { p = { x: rand(1, COLS - 2), y: rand(1, ROWS - 2) }; }
      while (snake.some(s => s.x === p.x && s.y === p.y));
      food = { ...p, born: performance.now() };
    }

    function rand(a, b) { return a + Math.floor(Math.random() * (b - a + 1)); }

    function message(html) { overlay.innerHTML = html; overlay.style.display = html ? "flex" : "none"; }

    // ------------------------------------------------------------------ input
    function turn(x, y) {
      if (state === "finishing" || state === "done") return;
      if (!started) { started = true; state = "play"; message(""); }
      if (paused) { paused = false; message(""); }
      const last = queue.length ? queue[queue.length - 1] : dir;
      if (last.x === -x && last.y === -y) return;     // niente inversione su se stesso
      if (last.x === x && last.y === y) return;
      if (queue.length < 3) queue.push({ x, y });
    }

    function onKey(e) {
      if (!container.isConnected || container.offsetParent === null) return;
      if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
      if (document.querySelector("dialog[open]")) return;      // una conferma è aperta
      const k = e.key.toLowerCase();
      const map = { arrowup: [0, -1], w: [0, -1], arrowdown: [0, 1], s: [0, 1],
                    arrowleft: [-1, 0], a: [-1, 0], arrowright: [1, 0], d: [1, 0] };
      if (map[k]) { e.preventDefault(); turn(...map[k]); }
      else if (k === " " && started && state === "play") {
        e.preventDefault();
        paused = !paused;
        message(paused ? `<div><b>Pausa</b><br>Gemini continua a lavorare per te.<br><small>Spazio per riprendere</small></div>` : "");
      }
    }

    let touchStart = null;
    function onTouchStart(e) { const t = e.touches[0]; touchStart = { x: t.clientX, y: t.clientY }; }
    function onTouchEnd(e) {
      if (!touchStart) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - touchStart.x, dy = t.clientY - touchStart.y;
      touchStart = null;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 20) { if (!started) turn(1, 0); return; }
      if (Math.abs(dx) > Math.abs(dy)) turn(Math.sign(dx), 0); else turn(0, Math.sign(dy));
      e.preventDefault();
    }

    window.addEventListener("keydown", onKey);
    canvas.addEventListener("touchstart", onTouchStart, { passive: true });
    canvas.addEventListener("touchend", onTouchEnd);
    overlay.addEventListener("click", () => { if (!started) turn(1, 0); });
    window.addEventListener("resize", resize);

    // ------------------------------------------------------------------ logica
    function step() {
      if (queue.length) dir = queue.shift();
      const head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y };
      const hitWall = head.x < 0 || head.y < 0 || head.x >= COLS || head.y >= ROWS;
      const hitSelf = snake.slice(0, -1).some(s => s.x === head.x && s.y === head.y);
      if (hitWall || hitSelf) return die();
      snake.unshift(head);
      if (head.x === food.x && head.y === food.y) {
        score++;
        scoreEl.textContent = score;
        if (score > record) { record = score; recordEl.textContent = record; saveRecord(record); }
        burst(center(head), 14, COLORS);
        floats.push({ ...center(head), text: BITES[Math.floor(Math.random() * BITES.length)], life: 1 });
        tickMs = Math.max(85, 150 - score * 3);
        placeFood();
      } else {
        snake.pop();
      }
    }

    function die() {
      state = "dead";
      flash = 1;
      snake.forEach((s, i) => { if (i % 2 === 0) burst(center(s), 3, ["#ff6b6b", "#ffd166"]); });
      message(`<div><b>Contesto perso!</b><br>Gemini ricomincia a leggere da capo…</div>`);
      setTimeout(() => {
        if (state !== "dead") return;          // nel frattempo il lavoro è finito
        reset();
        state = "play";
        message("");
      }, 1300);
    }

    function center(c) { return { x: c.x * cell + cell / 2, y: c.y * cell + cell / 2 }; }

    function burst(p, n, palette) {
      for (let i = 0; i < n; i++) {
        const a = Math.random() * Math.PI * 2, v = 1 + Math.random() * 3.5;
        particles.push({ x: p.x, y: p.y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1,
                         color: palette[i % palette.length], r: 2 + Math.random() * 3 });
      }
    }

    // ------------------------------------------------------------------ fine lavoro
    function finish(callback) {
      if (state === "finishing" || state === "done") return;
      onFinished = callback;
      state = "finishing";
      started = true;
      paused = false;
      message("");
      if (!snake || snake.length < 3) reset();
      finishAt = performance.now();
      setProgress(1, 1, "Fatto!");
      // in una scheda nascosta il browser sospende le animazioni: si prosegue comunque
      fallback = setTimeout(() => { if (state !== "done") { state = "done"; if (onFinished) onFinished(); } }, 3500);
    }

    function finishFrame(now) {
      const t = (now - finishAt) / 1000;
      const tail = snake[snake.length - 1];
      // 1) tre "cacchine" di indici escono dalla coda
      const wanted = t < 0.25 ? 0 : t < 0.5 ? 1 : t < 0.75 ? 2 : 3;
      if (tail) {
        // direzione in cui "punta" la coda: dal penultimo segmento all'ultimo
        const prev = snake[snake.length - 2] || { x: tail.x + dir.x, y: tail.y + dir.y };
        const back = { x: tail.x - prev.x, y: tail.y - prev.y };
        while (poops.length < wanted) {
          const pos = poopSpot(center(tail), back, poops.length);
          poops.push({ x: pos.x, y: pos.y, born: now });
          burst(pos, 6, ["#a8683a", "#ffd166"]);
        }
      }
      // 2) il serpente si gonfia e trema
      // 3) esplosione
      if (t > 1.15 && snake.length) {
        snake.forEach(s => burst(center(s), 8, COLORS.concat(["#ffffff", "#ffd166"])));
        snake = [];
        flash = 1.2;
        message(`<div class="gs-done"><b>Indicizzazione completata!</b><br>${poops.length} index.csv appena sfornati ✦</div>`);
      }
      if (t > 2.1 && state !== "done") {
        state = "done";
        clearTimeout(fallback);
        if (onFinished) onFinished();
      }
    }

    function setProgress(done, total, text) {
      const pct = total ? Math.round(100 * done / total) : 0;
      barEl.style.width = `${total ? pct : 0}%`;
      progressEl.textContent = text || (total ? `Gemini: ${done}/${total} pagine (${pct}%)` : "Gemini al lavoro…");
    }

    // ------------------------------------------------------------------ disegno
    function draw(now) {
      const w = cell * COLS, h = cell * ROWS;
      const bg = ctx.createLinearGradient(0, 0, w, h);
      bg.addColorStop(0, "#0d1226");
      bg.addColorStop(1, "#1b1036");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "rgba(255,255,255,0.05)";
      for (let x = 0; x < COLS; x++) for (let y = 0; y < ROWS; y++) ctx.fillRect(x * cell + cell / 2 - 1, y * cell + cell / 2 - 1, 2, 2);

      if (food && state !== "finishing" && state !== "done") drawPdf(food, now);
      poops.forEach(p => drawPoop(p, now));
      drawSnake(now);

      particles = particles.filter(p => p.life > 0);
      particles.forEach(p => {
        p.x += p.vx; p.y += p.vy; p.vy += 0.06; p.life -= 0.02;
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.fillStyle = p.color;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
      });
      ctx.globalAlpha = 1;

      floats = floats.filter(f => f.life > 0);
      floats.forEach(f => {
        f.y -= 0.6; f.life -= 0.015;
        ctx.globalAlpha = Math.max(0, f.life);
        ctx.fillStyle = "#ffffff";
        ctx.font = `600 ${Math.max(11, cell * 0.6)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(f.text, Math.min(Math.max(f.x, 60), w - 60), f.y);
      });
      ctx.globalAlpha = 1;

      if (flash > 0) {
        ctx.fillStyle = `rgba(255,255,255,${Math.min(0.6, flash * 0.5)})`;
        ctx.fillRect(0, 0, w, h);
        flash -= 0.05;
      }
    }

    function drawSnake(now) {
      if (!snake || !snake.length) return;
      const swell = state === "finishing" ? 1 + Math.min(0.35, (now - finishAt) / 3000) : 1;
      const shake = state === "finishing" && now - finishAt > 700 ? (Math.random() - 0.5) * 3 : 0;
      for (let i = snake.length - 1; i >= 0; i--) {
        const s = snake[i];
        const c = center(s);
        const color = COLORS[Math.min(COLORS.length - 1, Math.floor(i / Math.max(1, snake.length - 1) * (COLORS.length - 1)))];
        const size = (i === 0 ? cell * 0.95 : cell * (0.82 - 0.2 * i / snake.length)) * swell;
        ctx.fillStyle = color;
        roundRect(c.x - size / 2 + shake, c.y - size / 2 + shake, size, size, size * 0.35);
        ctx.fill();
      }
      // testa: la stellina di Gemini
      const head = center(snake[0]);
      const spin = state === "dead" ? 0 : Math.sin(now / 300) * 0.15;
      drawSparkle(head.x + shake, head.y + shake, cell * 0.42 * swell, "#ffffff", spin);
    }

    function drawSparkle(x, y, r, color, rot) {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(rot);
      ctx.fillStyle = color;
      ctx.shadowColor = "rgba(160, 190, 255, 0.9)";
      ctx.shadowBlur = r * 0.8;
      // stella a quattro punte con i lati concavi (come il simbolo di Gemini)
      ctx.beginPath();
      ctx.moveTo(r, 0);
      for (let i = 1; i <= 4; i++) {
        const a = i * Math.PI / 2;
        ctx.quadraticCurveTo(0, 0, Math.cos(a) * r, Math.sin(a) * r);
      }
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.restore();
    }

    function drawPdf(f, now) {
      const c = center(f);
      const bob = Math.sin((now - f.born) / 250) * cell * 0.06;
      const w = cell * 0.68, h = cell * 0.84, x = c.x - w / 2, y = c.y - h / 2 + bob;
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.fillRect(x + 2, y + 3, w, h);
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.moveTo(x, y); ctx.lineTo(x + w * 0.68, y); ctx.lineTo(x + w, y + h * 0.26);
      ctx.lineTo(x + w, y + h); ctx.lineTo(x, y + h); ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#c9cdd6";
      ctx.beginPath(); ctx.moveTo(x + w * 0.68, y); ctx.lineTo(x + w * 0.68, y + h * 0.26); ctx.lineTo(x + w, y + h * 0.26); ctx.fill();
      ctx.fillStyle = "#e5322d";
      ctx.fillRect(x - w * 0.1, y + h * 0.52, w * 0.9, h * 0.3);
      ctx.fillStyle = "#ffffff";
      ctx.font = `800 ${Math.max(6, cell * 0.24)}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText("PDF", x + w * 0.35, y + h * 0.75);
    }

    // posizione della i-esima cacchina: in fila dietro la coda, distanziate perche' le
    // etichette non si sovrappongano; se dietro c'e' il muro, prova le altre direzioni
    function poopSpot(c, back, i) {
      const w = cell * COLS, h = cell * ROWS, k = cell * (1.3 + i * 2.1);
      const dirs = [back, { x: -back.y, y: back.x }, { x: back.y, y: -back.x }, { x: -back.x, y: -back.y }];
      for (const d of dirs) {
        const x = c.x + d.x * k, y = c.y + d.y * k;
        if (x > cell && x < w - cell && y > cell && y < h - cell * 1.4) return { x, y };
      }
      return { x: Math.min(w - cell, Math.max(cell, c.x)), y: Math.min(h - cell * 1.4, Math.max(cell, c.y - k)) };
    }

    function drawPoop(p, now) {
      const age = Math.min(1, (now - p.born) / 200);
      const s = cell * 0.5 * (0.4 + 0.6 * age);
      ctx.fillStyle = "#7a4a26";
      [[0, s * 0.55, s * 0.95], [0, 0, s * 0.72], [0, -s * 0.48, s * 0.48]].forEach(([dx, dy, r]) => {
        ctx.beginPath(); ctx.ellipse(p.x + dx, p.y + dy, r, r * 0.6, 0, 0, Math.PI * 2); ctx.fill();
      });
      ctx.fillStyle = "#a8683a";
      ctx.beginPath(); ctx.ellipse(p.x - s * 0.25, p.y - s * 0.05, s * 0.18, s * 0.1, -0.4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.font = `700 ${Math.max(8, cell * 0.36)}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText("index.csv", p.x, p.y + s * 1.55 + cell * 0.3);
    }

    function roundRect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }

    // ------------------------------------------------------------------ ciclo
    function loop(now) {
      if (!container.isConnected) { destroy(); return; }     // la pagina è cambiata
      frame = requestAnimationFrame(loop);
      if (state === "play" && started && !paused && now - lastTick >= tickMs) {
        lastTick = now;
        step();
      }
      if (state === "finishing") finishFrame(now);
      draw(now);
    }

    function destroy() {
      cancelAnimationFrame(frame);
      clearTimeout(fallback);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", resize);
      container.innerHTML = "";
    }

    resize();
    reset();
    state = "ready";
    started = false;
    paused = false;
    lastTick = 0;
    message(`<div><b>Gemini sta leggendo i tuoi PDF.</b><br>Aiutalo: fagli mangiare le pagine!<br>
             <small>Premi una freccia (o tocca qui) per iniziare</small></div>`);
    frame = requestAnimationFrame(loop);

    return {
      setProgress,
      finish,
      get state() { return state; },
      get score() { return score; },
      destroy,
      // per i test
      _debug: { die, step, turn, get snake() { return snake; }, get food() { return food; }, get dir() { return dir; } },
    };
  }

  let styled = false;
  function injectStyle() {
    if (styled) return;
    styled = true;
    const css = `
      .gs-wrap { margin-top: .75rem; color: #e8ecff; background: #0b1020; border-radius: 10px; padding: 10px;
                 width: fit-content; max-width: 100%; box-sizing: border-box; font: 14px/1.4 system-ui, sans-serif; }
      .gs-hud { display: flex; gap: 1rem; align-items: baseline; flex-wrap: wrap; margin-bottom: 6px; }
      .gs-title { font-weight: 700; background: linear-gradient(90deg, #4285F4, #9B72CB, #D96570);
                  -webkit-background-clip: text; background-clip: text; color: transparent; }
      .gs-score, .gs-record { font-size: .9rem; color: #b9c2e8; }
      .gs-progress { position: relative; height: 18px; background: #1a2140; border-radius: 999px; overflow: hidden; margin-bottom: 8px; }
      .gs-progress > div { position: absolute; inset: 0 auto 0 0; width: 0; transition: width .5s;
                           background: linear-gradient(90deg, #4285F4, #9B72CB, #D96570); }
      .gs-progress > span { position: relative; display: block; text-align: center; font-size: .75rem; line-height: 18px; color: #fff; }
      .gs-stage { position: relative; display: inline-block; border-radius: 8px; overflow: hidden; touch-action: none; }
      .gs-stage canvas { display: block; }
      .gs-overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; text-align: center;
                    background: rgba(8, 10, 25, .55); color: #fff; padding: 1rem; cursor: pointer; }
      .gs-overlay small { color: #b9c2e8; }
      .gs-done b { font-size: 1.2rem; background: linear-gradient(90deg, #8ab4ff, #d7b4ff, #ffb4bc);
                   -webkit-background-clip: text; background-clip: text; color: transparent; }
      .gs-help { font-size: .75rem; color: #8790b8; margin-top: 6px; }`;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  window.GeminiSnake = { mount };
})();
