"use strict";
/* Auth + watchlist via Supabase. Exposes window.Account:
     Account.ready            -> config + SDK present
     Account.isLoggedIn()
     Account.isStarred(sym)
     Account.count()
     Account.toggleStar(sym)  -> add/remove (prompts login if needed)
     Account.requireLogin()   -> open the login modal
     Account.onChange(fn)     -> called when auth or watchlist changes
*/
window.Account = (function () {
  const URL = window.SUPABASE_URL;
  const KEY = window.SUPABASE_ANON_KEY;
  const ready = !!(URL && KEY && window.supabase && window.supabase.createClient);

  const state = { user: null, watchlist: new Set(), chartPrefs: null };
  const listeners = [];
  const prefsListeners = [];
  let client = null;
  let modal = null;

  const fire = () => listeners.forEach((fn) => { try { fn(); } catch (e) {} });
  const firePrefs = () => prefsListeners.forEach((fn) => { try { fn(); } catch (e) {} });

  // --- header auth area -------------------------------------------------- //
  function renderAuthArea() {
    const el = document.getElementById("auth-area");
    if (!el) return;
    if (!ready) { el.innerHTML = ""; return; }
    if (state.user) {
      el.innerHTML =
        `<span class="auth-email">${state.user.email || "account"}</span>` +
        `<button class="chip" id="logout-btn">Log out</button>`;
      el.querySelector("#logout-btn").onclick = () => client.auth.signOut();
    } else {
      el.innerHTML =
        `<button class="chip" id="signup-btn">Sign up</button>` +
        `<button class="chip active" id="login-btn">Log in</button>`;
      el.querySelector("#login-btn").onclick = () => openLogin("login");
      el.querySelector("#signup-btn").onclick = () => openLogin("signup");
    }
  }

  // --- login / sign-up modal -------------------------------------------- //
  let mode = "login"; // "login" | "signup"

  function setMode(m) {
    mode = m;
    modal.querySelector("#auth-title").textContent =
      m === "signup" ? "Create account" : "Log in";
    modal.querySelector("#login-submit").textContent =
      m === "signup" ? "Sign up" : "Log in";
    modal.querySelector("#switch-text").textContent =
      m === "signup" ? "Already have an account?" : "Need an account?";
    modal.querySelector("#switch-link").textContent =
      m === "signup" ? "Log in" : "Sign up";
    modal.querySelector("#login-pass").setAttribute(
      "autocomplete", m === "signup" ? "new-password" : "current-password");
    modal.querySelector("#login-err").textContent = "";
  }

  function buildModal() {
    modal = document.createElement("div");
    modal.className = "modal-backdrop hidden";
    modal.innerHTML = `
      <div class="modal">
        <h3 id="auth-title">Log in</h3>
        <form id="login-form">
          <input id="login-email" type="email" placeholder="Email"
                 autocomplete="username" required />
          <input id="login-pass" type="password" placeholder="Password (6+ chars)"
                 autocomplete="current-password" required minlength="6" />
          <div class="modal-err" id="login-err"></div>
          <div class="modal-actions">
            <button type="button" class="chip" id="login-cancel">Cancel</button>
            <button type="submit" class="chip active" id="login-submit">Log in</button>
          </div>
        </form>
        <div class="modal-switch">
          <span id="switch-text">Need an account?</span>
          <a href="#" id="switch-link">Sign up</a>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", (e) => { if (e.target === modal) closeLogin(); });
    modal.querySelector("#login-cancel").onclick = closeLogin;
    modal.querySelector("#switch-link").onclick = (e) => {
      e.preventDefault();
      setMode(mode === "login" ? "signup" : "login");
    };
    modal.querySelector("#login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = modal.querySelector("#login-email").value.trim();
      const password = modal.querySelector("#login-pass").value;
      const err = modal.querySelector("#login-err");

      if (mode === "signup") {
        err.textContent = "Creating account…";
        const { data, error } = await client.auth.signUp({ email, password });
        if (error) { err.textContent = error.message; return; }
        if (data.session) { err.textContent = ""; closeLogin(); }
        else {
          err.textContent = "Account created — check your email to confirm, then log in.";
          setMode("login");
        }
      } else {
        err.textContent = "Signing in…";
        const { error } = await client.auth.signInWithPassword({ email, password });
        err.textContent = error ? error.message : "";
        if (!error) closeLogin();
      }
    });
  }

  function openLogin(startMode) {
    if (!ready) return;
    if (!modal) buildModal();
    setMode(startMode === "signup" ? "signup" : "login");
    modal.classList.remove("hidden");
    const em = modal.querySelector("#login-email");
    if (em) em.focus();
  }
  function closeLogin() { if (modal) modal.classList.add("hidden"); }

  // --- watchlist --------------------------------------------------------- //
  async function loadWatchlist() {
    state.watchlist = new Set();
    if (state.user) {
      const { data, error } = await client.from("watchlist").select("symbol");
      if (!error && data) state.watchlist = new Set(data.map((r) => r.symbol));
    }
    fire();
  }

  async function toggleStar(sym) {
    if (!ready) return;
    if (!state.user) { openLogin(); return; }
    if (state.watchlist.has(sym)) {
      state.watchlist.delete(sym); fire();
      const { error } = await client.from("watchlist").delete().eq("symbol", sym);
      if (error) { state.watchlist.add(sym); fire(); }
    } else {
      state.watchlist.add(sym); fire();
      const { error } = await client.from("watchlist")
        .insert({ symbol: sym, user_id: state.user.id });
      if (error) { state.watchlist.delete(sym); fire(); }
    }
  }

  // --- chart preferences ------------------------------------------------- //
  async function loadPrefs() {
    state.chartPrefs = null;
    if (state.user) {
      const { data, error } = await client.from("prefs")
        .select("chart").eq("user_id", state.user.id).maybeSingle();
      if (!error && data) state.chartPrefs = data.chart || {};
      else state.chartPrefs = {};
    }
    firePrefs();
  }

  async function saveChartPrefs(chart) {
    if (!ready) return { error: { message: "unavailable" } };
    if (!state.user) { openLogin(); return { error: { message: "not signed in" } }; }
    const { error } = await client.from("prefs")
      .upsert({ user_id: state.user.id, chart, updated_at: new Date().toISOString() });
    if (!error) { state.chartPrefs = chart; firePrefs(); }
    return { error };
  }

  // --- init -------------------------------------------------------------- //
  if (ready) {
    client = window.supabase.createClient(URL, KEY);
    client.auth.getSession().then(({ data }) => {
      state.user = data.session ? data.session.user : null;
      renderAuthArea();
      loadWatchlist();
      loadPrefs();
    });
    client.auth.onAuthStateChange((_e, session) => {
      state.user = session ? session.user : null;
      renderAuthArea();
      loadWatchlist();
      loadPrefs();
    });
  }
  // Render the (possibly empty) auth area as soon as the DOM is ready.
  if (document.readyState !== "loading") renderAuthArea();
  else document.addEventListener("DOMContentLoaded", renderAuthArea);

  return {
    ready,
    isLoggedIn: () => !!state.user,
    isStarred: (s) => state.watchlist.has(s),
    count: () => state.watchlist.size,
    toggleStar,
    requireLogin: openLogin,
    onChange: (fn) => listeners.push(fn),
    getChartPrefs: () => state.chartPrefs || {},
    saveChartPrefs,
    onPrefsChange: (fn) => prefsListeners.push(fn),
  };
})();
