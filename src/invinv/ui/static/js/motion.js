// Small motions the pages share. Nothing here moves when the system asks for reduced motion.
(() => {
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const usd = (n) =>
    "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Count a dollar figure up (or down) to its new value. A new count stops the one running,
  // which then starts from the figure shown at that moment.
  window.invinvCount = (id, from, to, ms = 800) => {
    const el = document.getElementById(id);
    if (!el || reduced() || from === to) return;
    if (el._counting != null) from = el._shown ?? from;
    const token = (el._counting = (el._counting ?? 0) + 1);
    const start = performance.now();
    const step = (now) => {
      if (el._counting !== token) return;
      const t = Math.min((now - start) / ms, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      el._shown = from + (to - from) * eased;
      el.textContent = usd(el._shown);
      if (t < 1) requestAnimationFrame(step);
      else {
        el.textContent = usd(to);
        el._shown = to;
        el._counting = null;
      }
    };
    requestAnimationFrame(step);
  };

  // Period switches: a pill behind the chosen segment slides to the newly chosen one.
  const place = (group) => {
    const active = group.querySelector(".q-btn.bg-primary");
    let pill = group.querySelector(":scope > .seg-indicator");
    if (!pill) {
      pill = document.createElement("div");
      pill.className = "seg-indicator";
      group.appendChild(pill);
      group.classList.add("has-indicator");
    }
    if (!active) {
      pill.style.opacity = "0";
      return;
    }
    pill.style.opacity = "1";
    pill.style.width = active.offsetWidth + "px";
    pill.style.height = active.offsetHeight + "px";
    pill.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
  };
  let queued = false;
  const placeAll = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      document.querySelectorAll(".segmented").forEach(place);
    });
  };
  const start = () => {
    new MutationObserver(placeAll).observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    window.addEventListener("resize", placeAll);
    placeAll();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
