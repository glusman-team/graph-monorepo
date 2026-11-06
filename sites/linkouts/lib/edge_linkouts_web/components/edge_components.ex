defmodule EdgeLinkoutsWeb.EdgeComponents do
  @moduledoc """
  Presentation pieces shared by the edge pages.

  Everything here renders *data* with HEEx escaping: `segments/1` walks the
  `EdgeLinkouts.Display.Segment` tuples, so a value from an upstream KG can never become
  markup. The relationship diagram is plain HTML and CSS (no SVG, no JS): names wrap
  instead of being clipped to a box, which is what an SVG text element cannot do.

  Linkouts leave the site, so every rendered link says so: an arrow icon, and prose links
  carry a screen-reader note. Chips carry the resolver host in `title` instead, because
  thirty chips each announcing a sentence would bury the list.
  """

  use EdgeLinkoutsWeb, :html

  alias EdgeLinkouts.Codec
  alias EdgeLinkouts.Display.Prefixes

  # Field names as a reader knows them. The diff panel shares this table so a field reads the
  # same everywhere; anything unlisted falls back to its underscores turned to spaces, which is
  # still readable and never hides a field.
  @field_labels %{
    "id" => "Edge id",
    "subject" => "Subject identifier",
    "object" => "Object identifier",
    "subject_name" => "Subject name",
    "object_name" => "Object name",
    "original_subject" => "Subject as the source wrote it",
    "original_object" => "Object as the source wrote it",
    "predicate" => "Predicate",
    "category" => "Category",
    "subject_category" => "Subject category",
    "object_category" => "Object category",
    "regulatory_approvals" => "Regulatory approvals",
    "FDA_regulatory_approvals" => "Regulatory approvals",
    "publications" => "Product labels",
    "number_of_cases" => "FAERS case count",
    "clinical_approval_status" => "Clinical approval status",
    "knowledge_level" => "Knowledge level",
    "agent_type" => "Assertion method",
    "disease_context_qualifier" => "Disease context",
    "disease_context_qualifier_name" => "Disease context",
    "sources" => "Knowledge sources",
    "supporting_text" => "Supporting text",
    "has_supporting_studies" => "Supporting studies"
  }

  @doc "A stored field name as a reader knows it."
  def field_label(key), do: Map.get(@field_labels, key, String.replace(key, "_", " "))

  @doc "Rendered linkout segments: text runs and hyperlinks, escaped by the template."
  attr :segments, :list, required: true
  attr :note, :boolean, default: true, doc: "add the screen-reader note to links"

  def segments(assigns) do
    ~H"""
    <.segment :for={segment <- @segments} segment={segment} note={@note} />
    """
  end

  defp segment(%{segment: {:text, text}} = assigns) do
    assigns = assign(assigns, :text, text)
    ~H"{@text}"
  end

  # Emphasis: the primary knowledge source inside a source list, where bold carries the
  # distinction a "(primary)" parenthetical used to spell out.
  defp segment(%{segment: {:strong, inner}} = assigns) do
    assigns = assign(assigns, :inner, inner)

    ~H"""
    <strong class="linkout-strong"><.segments segments={@inner} note={@note} /></strong>
    """
  end

  defp segment(%{segment: {:link, href, label}} = assigns) do
    assigns = assign(assigns, href: href, label: label, host: host_of(href))

    ~H"""
    <a href={@href} class="ext-link" title={@host} target="_blank" rel="noopener noreferrer">{@label}<.icon
      name="hero-arrow-top-right-on-square"
      class="ext-icon"
    /><span :if={@note} class="sr-only"> (opens {@host} in a new tab)</span></a>
    """
  end

  # A labelled fold: one fact (a node's CURIE) behind a chip named for what it holds. Same
  # inline reveal as the list fold below; the button's data-more is the chip label itself,
  # so the toggle restores "curie", and data-less swaps it for "hide" while open.
  defp segment(%{segment: {:fold, label, hidden}} = assigns) do
    assigns = assign(assigns, hidden: hidden, label: label)

    ~H"""
    <span class="linkout-more linkout-fold">
      <button
        type="button"
        class="linkout-more-btn"
        aria-expanded="false"
        data-more={@label}
        data-less="hide"
      >
        {@label}
      </button>
      <span class="linkout-more-rest" hidden><.segments segments={@hidden} note={@note} /></span>
    </span>
    """
  end

  # A list fold: the items beyond the inline cap, revealed in place by a button. A native
  # <details> is block-shaped in some engines and opened a vertical gap inside the paragraph,
  # so the toggle is a plain inline <button> and the rest is an inline <span hidden> — neither
  # can introduce line breaks. assets/js/app.js does the reveal.
  defp segment(%{segment: {:more, hidden}} = assigns) do
    # The payload is [separator, item, separator, item, …], so the item count is half its length.
    assigns = assign(assigns, hidden: hidden, count: div(length(hidden), 2))

    ~H"""
    <span class="linkout-more">
      <button
        type="button"
        class="linkout-more-btn"
        aria-expanded="false"
        data-more={"Show #{@count} more"}
      >
        Show {@count} more
      </button>
      <span class="linkout-more-rest" hidden><.segments segments={@hidden} note={@note} /></span>
    </span>
    """
  end

  @doc """
  Subject card, predicate, object card, as wrapping HTML.

  The legacy SVG diagram clipped names at 24 characters; a relationship about
  "hydrochlorothiazide" deserves its whole name. Cards stack below the predicate on narrow
  screens, where a horizontal arrow would squeeze both boxes to nothing.
  """
  attr :doc, :map, required: true

  def diagram(assigns) do
    doc = assigns.doc

    assigns =
      assigns
      |> assign(:subject_name, node_label(doc, "subject"))
      |> assign(:subject_curie, doc["subject"])
      |> assign(:subject_href, Prefixes.url(doc["subject"]))
      |> assign(:subject_category, category(doc, "subject"))
      |> assign(:object_name, node_label(doc, "object"))
      |> assign(:object_curie, doc["object"])
      |> assign(:object_href, Prefixes.url(doc["object"]))
      |> assign(:object_category, category(doc, "object"))
      |> assign(:predicate, doc["predicate"] || "")
      |> assign(:predicate_label, humanize_predicate(doc["predicate"] || ""))
      |> assign(
        :summary,
        "This relationship: #{node_label(doc, "subject")} (#{doc["subject"]}) " <>
          "#{doc["predicate"]} #{node_label(doc, "object")} (#{doc["object"]})"
      )

    ~H"""
    <section class="edge-diagram" aria-label={@summary}>
      <.node_card
        role="Subject"
        name={@subject_name}
        curie={@subject_curie}
        href={@subject_href}
        category={@subject_category}
      />
      <div class="edge-rel" aria-hidden="true">
        <.icon name="hero-arrow-right" class="edge-rel-arrow" />
        <p class="edge-rel-predicate">{@predicate_label}</p>
        <code class="edge-rel-raw">{@predicate}</code>
      </div>
      <.node_card
        role="Object"
        name={@object_name}
        curie={@object_curie}
        href={@object_href}
        category={@object_category}
      />
    </section>
    """
  end

  attr :role, :string, required: true
  attr :name, :string, required: true
  attr :curie, :string, default: nil
  attr :href, :string, default: nil
  attr :category, :string, default: nil

  defp node_card(assigns) do
    assigns = assign(assigns, :host, host_of(assigns.href))

    ~H"""
    <div class="edge-node">
      <p class="edge-node-role">{@role}</p>
      <p class="edge-node-name">{@name}</p>
      <p :if={@category} class="edge-node-cat">{@category}</p>
      <p :if={@curie} class="edge-node-id">
        <code>{@curie}</code>
        <button type="button" class="copy-btn" data-copy={@curie} aria-label={"Copy #{@curie}"}>
          <.icon name="hero-clipboard" class="size-3.5" />
          <span class="copy-text">Copy</span>
        </button>
        <a
          :if={@href}
          class="ext-link"
          href={@href}
          title={@host}
          target="_blank"
          rel="noopener noreferrer"
        >
          {@host}<.icon name="hero-arrow-top-right-on-square" class="ext-icon" />
        </a>
      </p>
    </div>
    """
  end

  @doc """
  The stored versions as a timeline: oldest first, each labelled by its release number and
  how many fields that release changed, the selected one marked for assistive tech.
  """
  attr :edge_id, :string, required: true
  attr :history, :list, required: true
  attr :current, :string, required: true

  def version_timeline(assigns) do
    # The latest badge tags the newest stored release by numeric comparison, the same
    # Codec.newest_label rule the home page's pill row uses: a blob accumulated out of
    # version order still badges the true newest, and the two pages can never disagree.
    latest_label = Codec.newest_label(Enum.map(assigns.history, & &1.label))

    assigns = assign(assigns, :latest_label, latest_label)

    ~H"""
    <nav aria-label="Stored versions" class="timeline">
      <ol>
        <li :for={step <- @history}>
          <.link
            patch={~p"/edges/#{@edge_id}?version=#{step.key}"}
            class="timeline-step"
            title={step.key}
            aria-current={step.key == @current && "page"}
          >
            <span class="timeline-label">{step.label}</span>
            <span :if={step.changes && step.changes > 0} class="count-badge">
              {step.changes} change{if step.changes == 1, do: "", else: "s"}
            </span>
          </.link>
          <%!-- Beside the pill, not inside it: the same chip the home page's pill row uses.
               Reading order is "1.23.4, latest", so the chip trails its pill on the right. --%>
          <span :if={step.label == @latest_label} class="latest-badge">latest</span>
        </li>
      </ol>
    </nav>
    """
  end

  @doc """
  The changes between the previous and the selected version, grouped by kind and labelled in
  the reader's vocabulary. Colour never carries the diff alone: every line also shows a
  +/- marker and a visually-hidden word for screen readers. A `scope` narrows the panel to
  one stored field's entries, for releases that changed several things at once.
  """
  attr :prev_key, :string, default: nil
  attr :key_name, :string, default: nil
  attr :diff, :list, default: nil
  attr :edge_id, :string, default: nil, doc: "the page's edge id, for the per-field scope links"
  attr :scope, :string, default: nil, doc: "the stored field key to narrow to (?change=)"

  # Nothing to compare (the oldest stored version) or nothing that changed: no section at
  # all. A "Changes" heading over an empty panel is noise; the version timeline above it
  # already carries the release list. An unresolved previous version still renders, since
  # that is a data anomaly a reader can report, not an empty state.
  def diff_panel(%{prev_key: nil} = assigns), do: empty_diff_panel(assigns)
  def diff_panel(%{diff: []} = assigns), do: empty_diff_panel(assigns)

  def diff_panel(assigns) do
    {changes, scoped?} = scoped(assigns.diff, assigns.scope)

    assigns =
      assign(assigns,
        groups: group_changes(changes),
        scoped?: scoped?
      )

    ~H"""
    <section aria-label="Changes from the previous version" class="diff-panel">
      <h2 class="section-title">Changes</h2>
      <%= cond do %>
        <% is_nil(@prev_key) -> %>
          <p>
            This is the oldest stored version; there is nothing earlier to compare it with.
          </p>
        <% is_nil(@diff) -> %>
          <p>
            The previous version ({version_label(@prev_key)}) could not be resolved, so no diff is shown.
          </p>
        <% @diff == [] -> %>
          <p>No changes from {version_label(@prev_key)} to this version.</p>
        <% true -> %>
          <%!-- Scoped, the summary still counts the whole release's changes, so the reader
               knows how much is hidden behind the scope. --%>
          <p class="diff-summary">
            <%= if @scoped? do %>
              Showing only {field_label(@scope)} of the {diff_summary(@diff, @prev_key, @key_name)}.
              <.link patch={~p"/edges/#{@edge_id}?version=#{@key_name}"} class="diff-scope-clear">
                Show every change
              </.link>
            <% else %>
              {diff_summary(@diff, @prev_key, @key_name)}
            <% end %>
          </p>
          <div :for={{title, entries} <- @groups} class="diff-group">
            <%!-- The group heading is taxonomy, not reader information: the +/- markers and
               field labels carry the meaning. It earns its line only when one heading
               batches several entries; a single-entry group starts straight at the field. --%>
            <h3 :if={length(entries) > 1}>{title}</h3>
            <ul>
              <.diff_entry
                :for={change <- entries}
                change={change}
                edge_id={@edge_id}
                key_name={@key_name}
              />
            </ul>
          </div>
      <% end %>
    </section>
    """
  end

  defp empty_diff_panel(assigns) do
    ~H"""
    <%!-- nothing to compare and nothing that changed render no section at all --%>
    """
  end

  # A ?change= scope narrows the panel to one stored field's entries. A scope matching
  # nothing (a stale link after the graph reingested) is ignored: the full diff renders
  # rather than an empty panel pretending nothing changed.
  defp scoped(nil, _scope), do: {nil, false}

  defp scoped(diff, scope) when is_binary(scope) do
    matching = Enum.filter(diff, &(field_key(&1) == scope))

    if matching == [], do: {diff, false}, else: {matching, true}
  end

  defp scoped(diff, _scope), do: {diff, false}

  defp field_key({:added, key, _}), do: key
  defp field_key({:removed, key, _}), do: key
  defp field_key({:changed, key, _, _}), do: key
  defp field_key({:list_changed, key, _, _}), do: key

  defp group_changes(nil), do: []

  defp group_changes(diff) do
    diff
    |> Enum.group_by(fn
      {:added, _, _} -> "Added fields"
      {:removed, _, _} -> "Removed fields"
      {:changed, _, _, _} -> "Changed values"
      {:list_changed, _, _, _} -> "Changed lists"
    end)
    |> Enum.sort_by(fn {title, _} -> title end)
  end

  defp diff_entry(%{change: {:added, key, new}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        values: values(new),
        scope_href: scope_href(assigns, key)
      )

    ~H"""
    <li class="diff-line-added">
      <span aria-hidden="true">+</span><span class="sr-only"> added </span>
      <.diff_label href={@scope_href} label={field_label(@key)} />
      <code :for={value <- @values}>{value}</code>
    </li>
    """
  end

  defp diff_entry(%{change: {:removed, key, old}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        values: values(old),
        scope_href: scope_href(assigns, key)
      )

    ~H"""
    <li class="diff-line-removed">
      <span aria-hidden="true">-</span><span class="sr-only"> removed </span>
      <.diff_label href={@scope_href} label={field_label(@key)} />
      <code :for={value <- @values}>{value}</code>
    </li>
    """
  end

  defp diff_entry(%{change: {:changed, key, old, new}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        olds: values(old),
        news: values(new),
        scope_href: scope_href(assigns, key)
      )

    ~H"""
    <li class="diff-line-removed">
      <span aria-hidden="true">-</span><span class="sr-only"> removed </span>
      <.diff_label href={@scope_href} label={field_label(@key)} />
      <code :for={value <- @olds} class="diff-old">{value}</code>
    </li>
    <li class="diff-line-added">
      <span aria-hidden="true">+</span><span class="sr-only"> added </span>
      <.diff_label href={@scope_href} label={field_label(@key)} />
      <code :for={value <- @news} class="diff-new">{value}</code>
    </li>
    """
  end

  defp diff_entry(%{change: {:list_changed, key, added, removed}} = assigns) do
    {inner, parent} = inner_object_diff(added, removed)

    assigns =
      assign(assigns,
        key: key,
        inner: inner,
        path: parent && "#{key}.#{parent}",
        added: Enum.map(added, &pretty/1),
        removed: Enum.map(removed, &pretty/1),
        scope_href: scope_href(assigns, key)
      )

    ~H"""
    <li class="diff-line-changed">
      <%!-- When exactly one object left the list and one entered it, the whole-object chips
           bury the fact: nearly always one field inside the object moved. Render those inner
           fields as their own +/- lines, addressed by the full dotted path to the moved
           value ("sources.infores:drugapprovals-kp.source_record_urls"), so the reader sees
           which entry and field changed however many sources the list holds; anything less
           symmetric falls back to whole-element chips, which stay honest for reorderings
           and multi-element changes. --%>
      <ul :if={@inner != []} class="diff-list">
        <.inner_field
          :for={field <- @inner}
          field={field}
          path={@path}
          edge_id={@edge_id}
          key_name={@key_name}
          scope_key={@key}
        />
      </ul>
      <ul :if={@inner == []} class="diff-list">
        <li :for={item <- @added} class="diff-line-added">
          <span aria-hidden="true">+</span><span class="sr-only"> added </span>
          <.diff_label href={@scope_href} label={field_label(@key)} />
          <code>{item}</code>
        </li>
        <li :for={item <- @removed} class="diff-line-removed">
          <span aria-hidden="true">-</span><span class="sr-only"> removed </span>
          <.diff_label href={@scope_href} label={field_label(@key)} />
          <code>{item}</code>
        </li>
      </ul>
    </li>
    """
  end

  # Exactly one map out, one map in: the pair is diffable field by field, and the map's own
  # resource_id is the path segment the inner lines address through. Anything else
  # (multi-element changes, reorderings, scalar elements) returns the empty field list and no
  # path, and the caller renders whole-element chips instead.
  defp inner_object_diff([new], [old]) when is_map(new) and is_map(old) do
    keys = old |> Map.keys() |> Kernel.++(Map.keys(new)) |> Enum.uniq() |> Enum.sort()

    fields =
      for key <- keys,
          change = inner_field_change(key, Map.get(old, key), Map.get(new, key)),
          do: change

    {fields, Map.get(new, "resource_id")}
  end

  defp inner_object_diff(_added, _removed), do: {[], nil}

  defp inner_field_change(_key, same, same), do: nil
  defp inner_field_change(key, nil, new), do: {:inner_added, key, values(new)}
  defp inner_field_change(key, old, nil), do: {:inner_removed, key, values(old)}
  defp inner_field_change(key, old, new), do: {:inner_changed, key, values(old), values(new)}

  defp inner_field(%{field: {:inner_added, key, values}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        values: values,
        label: "#{assigns.path}.#{key}",
        scope_href: scope_href(assigns, assigns.scope_key)
      )

    ~H"""
    <li class="diff-line-added">
      <span aria-hidden="true">+</span><span class="sr-only"> added </span>
      <.diff_label href={@scope_href} label={@label} klass="diff-field diff-path" />
      <code :for={value <- @values}>{value}</code>
    </li>
    """
  end

  defp inner_field(%{field: {:inner_removed, key, values}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        values: values,
        label: "#{assigns.path}.#{key}",
        scope_href: scope_href(assigns, assigns.scope_key)
      )

    ~H"""
    <li class="diff-line-removed">
      <span aria-hidden="true">-</span><span class="sr-only"> removed </span>
      <.diff_label href={@scope_href} label={@label} klass="diff-field diff-path" />
      <code :for={value <- @values}>{value}</code>
    </li>
    """
  end

  defp inner_field(%{field: {:inner_changed, key, old_values, new_values}} = assigns) do
    assigns =
      assign(assigns,
        key: key,
        label: "#{assigns.path}.#{key}",
        olds: old_values,
        news: new_values,
        scope_href: scope_href(assigns, assigns.scope_key)
      )

    ~H"""
    <li class="diff-line-removed">
      <span aria-hidden="true">-</span><span class="sr-only"> removed </span>
      <.diff_label href={@scope_href} label={@label} klass="diff-field diff-path" />
      <code :for={value <- @olds} class="diff-old">{value}</code>
    </li>
    <li class="diff-line-added">
      <span aria-hidden="true">+</span><span class="sr-only"> added </span>
      <.diff_label href={@scope_href} label={@label} klass="diff-field diff-path" />
      <code :for={value <- @news} class="diff-new">{value}</code>
    </li>
    """
  end

  # The field label doubles as the scoping link: with several changes in one release a
  # reader can narrow the panel to that one field. It stays quiet (the label reads as data,
  # not chrome); hover reveals the link, and the title says what clicking does. Without a
  # scope href it renders as the plain label it always was.
  attr :href, :string, default: nil
  attr :label, :string, required: true
  attr :klass, :string, default: "diff-field"

  defp diff_label(%{href: nil} = assigns) do
    ~H"""
    <span class={@klass}>{@label}</span>
    """
  end

  defp diff_label(assigns) do
    ~H"""
    <.link patch={@href} class={@klass} title={"Show only the changes to #{@label}"}>{@label}</.link>
    """
  end

  # The scope target for one stored field key: the same page and version, plus ?change=.
  defp scope_href(%{edge_id: id, key_name: key}, field) when is_binary(id) and is_binary(key),
    do: ~p"/edges/#{id}?version=#{key}&change=#{field}"

  defp scope_href(_assigns, _field), do: nil

  # "1 field changed from 1.16.0 to 1.11.2": one uninterrupted string, so the summary reads
  # as a sentence no matter how the template lays out around the interpolation.
  defp diff_summary(diff, prev_key, key_name) do
    noun = if(length(diff) == 1, do: "field", else: "fields")

    "#{length(diff)} #{noun} changed from #{version_label(prev_key)} to #{version_label(key_name)}"
  end

  # A stored key carries the KG name on every mention ("drugapprovals-kp-1.23.3"), which is
  # noise once the page already says which KG it is. The release number is the comparable part.
  defp version_label(key) do
    case EdgeLinkouts.Display.name_of(key) do
      nil -> key
      name -> String.replace_prefix(key, name <> "-", "")
    end
  end

  @doc """
  Fallback rendering for a blob whose version key has no display config: the raw KGX
  fields, so an unmapped knowledge graph still renders instead of a 500.
  """
  attr :doc, :map, required: true

  def raw_doc(assigns) do
    assigns = assign(assigns, fields: assigns.doc |> Map.to_list() |> Enum.sort())

    ~H"""
    <p class="raw-doc-notice opacity-80">
      This knowledge graph has no display configuration, so the stored fields are shown as-is.
    </p>
    <dl>
      <div :for={{key, value} <- @fields} class="py-1">
        <dt class="font-semibold">{key}</dt>
        <dd><code>{JSON.encode!(value)}</code></dd>
      </div>
    </dl>
    """
  end

  defp node_label(doc, field) do
    doc["#{field}_name"] || doc[field] || ""
  end

  defp category(doc, field) do
    case doc["#{field}_category"] do
      [first | _] when is_binary(first) -> humanize_category(first)
      _ -> nil
    end
  end

  # "biolink:SmallMolecule" reads as "Small molecule" once the prefix, the camel humps and
  # the underscores go. The raw CURIE category stays in the document for anyone who needs it.
  defp humanize_category(value) do
    value
    |> humanize_predicate()
    |> String.replace(~r/(?<=[a-z0-9])([A-Z])/, " \\1")
    |> then(fn text -> String.capitalize(text) end)
  end

  # "biolink:applied_to_treat" reads as prose once the prefix and underscores go; the raw
  # predicate stays visible beside it because the exact term is what a curator checks.
  defp humanize_predicate(value) do
    value
    |> String.replace(~r/^biolink:/i, "")
    |> String.replace("_", " ")
  end

  # The resolver's host, so a linkout says where it goes before it is clicked.
  defp host_of(nil), do: nil

  defp host_of(href) do
    case URI.parse(href) do
      %URI{host: host} when is_binary(host) -> String.replace_prefix(host, "www.", "")
      _ -> href
    end
  end

  # Strings print as themselves; everything else keeps its JSON shape, so a nested map in a
  # diff is visibly a nested map rather than a quoted blob.
  defp pretty(value) when is_binary(value), do: value
  defp pretty(value) when is_number(value), do: to_string(value)
  defp pretty(value), do: JSON.encode!(value)

  # A changed list reads as one chip per element; a scalar reads as one chip. A JSON array
  # printed inline is the wall of text the evidence panel was redesigned to avoid.
  defp values(list) when is_list(list), do: Enum.map(list, &pretty/1)
  defp values(value), do: [pretty(value)]
end
