// <showntell-player src="https://…/w/ID/" poster="poster.png" frame="landscape" speed="1.25"></showntell-player>
//
// Embeds a walkthrough (a live link or an exported folder) in any page. The player runs in an
// iframe; this element forwards its events and commands:
//   events:  ready, slidechange, timeupdate, durationchange, waiting, play, pause, ended,
//            playbackerror, error   (as CustomEvents; the data is in event.detail)
//   methods: play(), pause(), seek(seconds)
//   props:   currentTime, duration, paused, slide
// With `poster`, the iframe is not loaded until the poster is clicked, so a page can hold many
// players cheaply. Mobile browsers only start audio from a tap inside the player, so the player's
// own play button may need one more tap there. `speed` starts the player at that speed (0.5 to 3).

const RATIOS = { landscape: "16 / 9", square: "1 / 1", portrait: "9 / 16" };

class ShowntellPlayer extends HTMLElement {
  static observedAttributes = ["src", "frame", "poster", "speed"];

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.state = { currentTime: 0, duration: 0, paused: true, slide: null };
    this._onMessage = this._onMessage.bind(this);
  }

  connectedCallback() {
    window.addEventListener("message", this._onMessage);
    this._render();
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
  }

  attributeChangedCallback() {
    if (this.isConnected) this._render();
  }

  get currentTime() { return this.state.currentTime; }
  get duration() { return this.state.duration; }
  get paused() { return this.state.paused; }
  get slide() { return this.state.slide; }

  play() { this._load(true); this._send({ type: "play" }); }
  pause() { this._send({ type: "pause" }); }
  seek(t) { this._send({ type: "seek", t: Number(t) }); }

  _url(autoplay) {
    const u = new URL(this.getAttribute("src") || "", document.baseURI);
    u.searchParams.set("embed", "1");
    if (autoplay) u.searchParams.set("autoplay", "1");
    if (this.hasAttribute("speed")) u.searchParams.set("speed", this.getAttribute("speed"));
    return u.toString();
  }

  _render() {
    const ratio = RATIOS[this.getAttribute("frame")] || RATIOS.landscape;
    const poster = this.getAttribute("poster");
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; position: relative; aspect-ratio: ${ratio}; background: #0a0c10; border-radius: 12px; overflow: hidden; }
        iframe, button { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
        button { padding: 0; cursor: pointer; background: #0a0c10 center / cover no-repeat; }
        button span { position: absolute; left: 50%; top: 50%; width: 76px; height: 76px; margin: -38px 0 0 -38px; border-radius: 50%;
          background: #ffc857; display: grid; place-items: center; box-shadow: 0 10px 34px rgba(0,0,0,.45); }
      </style>`;
    if (poster) {
      const b = document.createElement("button");
      b.setAttribute("aria-label", "Play walkthrough");
      b.style.backgroundImage = `url("${new URL(poster, document.baseURI)}")`;
      b.innerHTML = '<span><svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true"><path d="M8 5v14l11-7z" fill="#1a1406"/></svg></span>';
      b.onclick = () => this._load(true);
      this.shadowRoot.append(b);
    } else {
      this._load(false);
    }
  }

  _load(autoplay) {
    if (this.frameEl) return;
    this.shadowRoot.querySelector("button")?.remove();
    const f = document.createElement("iframe");
    f.title = this.getAttribute("title") || "Walkthrough";
    f.allow = "autoplay; fullscreen; clipboard-write";
    f.src = this._url(autoplay);
    this.frameEl = f;
    this.shadowRoot.append(f);
  }

  _send(m) {
    this.frameEl?.contentWindow?.postMessage({ target: "showntell", ...m }, "*");
  }

  _onMessage(e) {
    const m = e.data;
    if (!this.frameEl || e.source !== this.frameEl.contentWindow || !m || m.source !== "showntell") return;
    const { source, type, ...detail } = m;
    if ("currentTime" in detail) this.state.currentTime = detail.currentTime;
    if ("duration" in detail) this.state.duration = detail.duration;
    if (type === "play") this.state.paused = false;
    if (type === "pause" || type === "ended") this.state.paused = true;
    if (type === "slidechange") this.state.slide = detail;
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

if (!customElements.get("showntell-player")) customElements.define("showntell-player", ShowntellPlayer);
