// If you want to use Phoenix channels, run `mix help phx.gen.channel`
// to get started and then uncomment the line below.
// import "./user_socket.js"

// You can include dependencies in two ways.
//
// The simplest option is to put them in assets/vendor and
// import them using relative paths:
//
//     import "../vendor/some-package.js"
//
// Alternatively, you can `npm install some-package --prefix assets` and import
// them using a path starting with the package name:
//
//     import "some-package"
//
// If you have dependencies that try to import CSS, esbuild will generate a separate `app.css` file.
// To load it, simply add a second `<link>` to your `root.html.heex` file.

// Include phoenix_html to handle method=PUT/DELETE in forms and buttons.
import "phoenix_html"
// Establish Phoenix Socket and LiveView configuration.
import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"
import {hooks as colocatedHooks} from "phoenix-colocated/edge_linkouts"
import topbar from "../vendor/topbar"

// Dark mode toggle (lib/edge_linkouts_web/components/layouts.ex): flips data-theme on
// <html> and persists it. The root layout's inline script applied the initial theme
// before first paint; while nothing is stored, OS preference changes still apply.
const Theme = {
  mounted() {
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    media.addEventListener("change", (event) => {
      if (!localStorage.getItem("theme")) {
        document.documentElement.setAttribute("data-theme", event.matches ? "dark" : "light")
      }
    })

    this.el.addEventListener("click", () => {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"
      localStorage.setItem("theme", next)
      document.documentElement.setAttribute("data-theme", next)
    })
  },
}

// Copy affordance for any [data-copy] button: the attribute is the exact text to copy
// (an edge id, a CURIE), so what lands on the clipboard is never what the eye truncated.
// The button says "Copied" for a moment, because a silent copy reads as a dead button.
// Delegated on document so LiveView re-renders never detach it.
document.addEventListener("click", (event) => {
  const button = event.target.closest(".linkout-more-btn")
  if (!button) return

  // The rest is an inline <span hidden> next to the button: toggling `hidden` reveals the
  // remaining list items in the middle of the sentence, with no reflow outside the line.
  const rest = button.parentElement && button.parentElement.querySelector(".linkout-more-rest")
  if (!rest) return

  const willOpen = rest.hasAttribute("hidden")
  if (willOpen) {
    rest.removeAttribute("hidden")
    button.setAttribute("aria-expanded", "true")
    button.textContent = button.dataset.less || "Show less"
  } else {
    rest.setAttribute("hidden", "")
    button.setAttribute("aria-expanded", "false")
    button.textContent = button.dataset.more
  }
})

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]")
  if (!button || !("clipboard" in navigator)) return

  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    const label = button.querySelector(".copy-text")
    if (!label) return
    const original = label.textContent
    label.textContent = "Copied"
    button.classList.add("is-copied")
    setTimeout(() => {
      label.textContent = original
      button.classList.remove("is-copied")
    }, 1200)
  })
})

// Overflow for the KG bar's version pills (page_html/kg_bar.html.heex). The pills sit on one
// measured line; whatever does not fit the window hides behind a "…" chip that expands the row
// on click. Pixel measurement is the only client-side work this app does: the server renders
// every pill, and it cannot know the reader's window width or font metrics.
function fitVersionPills(row) {
  const more = row.querySelector("[data-kg-more]")
  // An expanded row stays expanded across resizes; collapsing what a reader asked to see would
  // undo their click every time the window moves.
  if (!more || row.classList.contains("is-expanded")) return

  const pills = [...row.querySelectorAll(".version-pill")]
  pills.forEach((pill) => pill.classList.remove("is-hidden"))
  more.hidden = true
  if (row.scrollWidth <= row.clientWidth) return

  // The chip takes width too, so it goes on before the measuring rather than after.
  more.hidden = false
  let hidden = 0
  // i > 0: the newest release always stays visible, so a very narrow window shows one pill and
  // the chip rather than a chip on its own.
  for (let i = pills.length - 1; i > 0 && row.scrollWidth > row.clientWidth; i--) {
    pills[i].classList.add("is-hidden")
    hidden += 1
  }

  more.hidden = hidden === 0
  more.setAttribute("aria-expanded", "false")
  more.setAttribute("aria-label", `Show all ${pills.length} releases (${hidden} hidden)`)
}

function fitAllVersionPills() {
  document.querySelectorAll("[data-kg-pills]").forEach(fitVersionPills)
}

// Delegated on document, like the copy handler above, so it survives any re-render.
document.addEventListener("click", (event) => {
  const more = event.target.closest("[data-kg-more]")
  if (!more) return

  const row = more.closest("[data-kg-pills]")
  if (!row) return

  row.classList.add("is-expanded")
  row
    .querySelectorAll(".version-pill.is-hidden")
    .forEach((pill) => pill.classList.remove("is-hidden"))
  more.hidden = true
  more.setAttribute("aria-expanded", "true")
})

// Re-measure on resize, at most once per frame: a drag fires dozens of events.
let pillFrame = null
window.addEventListener("resize", () => {
  if (pillFrame) cancelAnimationFrame(pillFrame)
  pillFrame = requestAnimationFrame(() => {
    pillFrame = null
    fitAllVersionPills()
  })
})

// This file is a module, so the DOM is parsed by the time it runs and the bar (a static page,
// no LiveView) can be measured immediately. Web fonts change the metrics, so measure again
// once they land — measuring with fallback fonts can hide a pill that would have fit.
fitAllVersionPills()
if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAllVersionPills)

const csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")
const liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: {...colocatedHooks, Theme},
})

// Show progress bar on live navigation and form submits
topbar.config({barColors: {0: "#29d"}, shadowColor: "rgba(0, 0, 0, .3)"})
window.addEventListener("phx:page-loading-start", _info => topbar.show(300))
window.addEventListener("phx:page-loading-stop", _info => topbar.hide())

// connect if there are any LiveViews on the page
liveSocket.connect()

// expose liveSocket on window for web console debug logs and latency simulation:
// >> liveSocket.enableDebug()
// >> liveSocket.enableLatencySim(1000)  // enabled for duration of browser session
// >> liveSocket.disableLatencySim()
window.liveSocket = liveSocket

// The lines below enable quality of life phoenix_live_reload
// development features:
//
//     1. stream server logs to the browser console
//     2. click on elements to jump to their definitions in your code editor
//
if (process.env.NODE_ENV === "development") {
  window.addEventListener("phx:live_reload:attached", ({detail: reloader}) => {
    // Enable server log streaming to client.
    // Disable with reloader.disableServerLogs()
    reloader.enableServerLogs()

    // Open configured PLUG_EDITOR at file:line of the clicked element's HEEx component
    //
    //   * click with "c" key pressed to open at caller location
    //   * click with "d" key pressed to open at function component definition location
    let keyDown
    window.addEventListener("keydown", e => keyDown = e.key)
    window.addEventListener("keyup", _e => keyDown = null)
    window.addEventListener("click", e => {
      if(keyDown === "c"){
        e.preventDefault()
        e.stopImmediatePropagation()
        reloader.openEditorAtCaller(e.target)
      } else if(keyDown === "d"){
        e.preventDefault()
        e.stopImmediatePropagation()
        reloader.openEditorAtDef(e.target)
      }
    }, true)

    window.liveReloader = reloader
  })
}
