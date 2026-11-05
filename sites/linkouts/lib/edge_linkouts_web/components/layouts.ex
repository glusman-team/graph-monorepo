defmodule EdgeLinkoutsWeb.Layouts do
  @moduledoc """
  This module holds layouts and related functionality used by your application.
  """
  use EdgeLinkoutsWeb, :html

  # Embed all files in layouts/* within this module.
  embed_templates "layouts/*"

  @doc """
  Renders your app layout: the site header and the main landmark every page renders into.

  `kg_slug` scopes the header's "Random edge" button to one knowledge graph. A page that is
  about a graph passes its slug and the button picks a random edge *from that graph's latest
  release*; with no slug the button picks from the latest release of every graph in the store.
  Reading a page about one KG and then landing on an edge from another is the kind of surprise
  that makes a button untrustworthy.

  `random_version` pins that pick further, to one release of the scoped graph. An edge page
  passes the release it is showing, so every random click stays inside the version the reader
  is reading until they switch versions or use the global randomizer. Without it the button
  draws from the graph's newest stored release, never an older one.

  ## Examples

      <Layouts.app flash={@flash}>
        <h1>Content</h1>
      </Layouts.app>

      <Layouts.app flash={@flash} kg_slug="drugapprovals-kp" random_version="1.23.4">
        <h1>Content</h1>
      </Layouts.app>

  """
  attr :flash, :map, required: true, doc: "the map of flash messages"
  attr :kg_slug, :string, default: nil, doc: "scopes the random button to one knowledge graph"

  attr :random_version, :string,
    default: nil,
    doc: "pins the random button to one release of the scoped graph (a bare label, \"1.23.4\")"

  slot :inner_block, required: true

  def app(assigns) do
    ~H"""
    <p :if={Application.get_env(:edge_linkouts, :fixture_preview, false)} class="preview-notice">
      <strong>DEV preview</strong> Sample data only, not the live knowledge graph.
    </p>
    <p class="research-notice" role="note" aria-label="Research use only">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        stroke-width="2"
        stroke="currentColor"
        class="size-4"
        aria-hidden="true"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
        />
      </svg>
      <span>
        <strong>For research use.</strong>
        These pages describe what a knowledge graph asserts; they are not clinical advice and
        must not guide the treatment of a patient.
      </span>
    </p>
    <header class="site-header">
      <nav aria-label="Site" class="site-header-inner">
        <.link href={~p"/"} class="brand">TranslatorEdgeLinkouts</.link>
        <div class="flex items-center gap-2">
          <a class="btn btn-primary btn-sm random-btn" href={random_href(@kg_slug, @random_version)}>
            <%!-- A die, not sparkles: this picks a stored edge at random, and sparkles read
                 as "AI generated" rather than "chance". --%>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke-width="1.8"
              stroke="currentColor"
              class="size-4"
              aria-hidden="true"
            >
              <rect x="3.5" y="3.5" width="17" height="17" rx="3.5" />
              <circle cx="8.5" cy="8.5" r="1.5" fill="currentColor" stroke="none" />
              <circle cx="15.5" cy="8.5" r="1.5" fill="currentColor" stroke="none" />
              <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
              <circle cx="8.5" cy="15.5" r="1.5" fill="currentColor" stroke="none" />
              <circle cx="15.5" cy="15.5" r="1.5" fill="currentColor" stroke="none" />
            </svg>
            <span>Random edge</span>
          </a>
          <.theme_toggle />
        </div>
      </nav>
    </header>

    <main class="mx-auto w-full max-w-5xl px-8 py-8">
      {render_slot(@inner_block)}
    </main>

    <footer class="site-footer">
      <p>
        Every page here is one stored relationship, rendered from the knowledge graph's own
        fields, with the evidence and the versions behind it.
      </p>
    </footer>

    <.flash_group flash={@flash} />
    """
  end

  @doc """
  Shows the flash group with standard titles and content.

  ## Examples

      <.flash_group flash={@flash} />
  """
  attr :flash, :map, required: true, doc: "the map of flash messages"
  attr :id, :string, default: "flash-group", doc: "the optional id of flash container"

  def flash_group(assigns) do
    ~H"""
    <div id={@id} aria-live="polite">
      <.flash kind={:info} flash={@flash} />
      <.flash kind={:error} flash={@flash} />
    </div>
    """
  end

  # Two-state dark-mode toggle. The button carries phx-hook="Theme" (assets/js/app.js):
  # clicking flips data-theme on <html> and persists it in localStorage; with nothing
  # stored the page follows prefers-color-scheme.
  def theme_toggle(assigns) do
    ~H"""
    <button
      type="button"
      id="theme-toggle"
      phx-hook="Theme"
      aria-label="Toggle dark mode"
      class="btn btn-ghost btn-sm"
    >
      <.icon name="hero-moon-micro" class="size-4 dark:hidden" />
      <.icon name="hero-sun-micro" class="hidden size-4 dark:block" />
    </button>
    """
  end

  # The random button's target: one graph when the page is about one, the whole store
  # otherwise; one release when the page shows one, every stored release otherwise.
  defp random_href(nil, _version), do: ~p"/random"

  defp random_href(slug, nil), do: ~p"/#{slug}/random"
  defp random_href(slug, version), do: ~p"/#{slug}/random?version=#{version}"
end
