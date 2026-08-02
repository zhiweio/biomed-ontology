import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";

const dark = window.matchMedia("(prefers-color-scheme: dark)").matches
  || document.body.getAttribute("data-md-color-scheme") === "slate";

mermaid.initialize({
  startOnLoad: true,
  theme: dark ? "dark" : "default",
  securityLevel: "loose",
});

document$.subscribe(() => {
  mermaid.run({ querySelector: ".mermaid" });
});
