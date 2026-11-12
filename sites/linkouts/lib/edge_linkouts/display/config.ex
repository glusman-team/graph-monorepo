defmodule EdgeLinkouts.Display.Config do
  @moduledoc """
  The shape of a KG display config, and its validation.

  Configs live in `kgs/*.exs` as plain Elixir terms and are loaded at compile time. They contain
  no code — only data in the grammar `EdgeLinkouts.Display.Value` interprets — so a curator can
  add a knowledge graph without touching the application, and `mix linkouts.check` can prove a
  config is well-formed without rendering a page.

  Validation is recursive over the value grammar, not just over the top-level keys. That matters
  because the failure mode of a typo'd spec is otherwise a blank sentence in production: a
  `{:pick, ...}` naming a field the release renamed renders nothing, and nothing looks like
  missing data.

  The key reference below is the schema; the guide "The default config" (`the-default-config.html`
  under Guides) walks what the shared base renders, slot by slot, with worked examples.
  """

  alias EdgeLinkouts.Display.Version

  @schema NimbleOptions.new!(
            name: [
              type: :string,
              required: true,
              doc:
                "The KGX name, as in `<name>-<version>` keys. Must match the key prefix the worker loads."
            ],
            display_name: [
              type: {:custom, __MODULE__, :validate_optional_string, []},
              default: nil,
              doc:
                "Human name shown in the edge page header. Omitted by the default config, whose " <>
                  "header names the graph from the stored version key instead."
            ],
            extends: [
              type: {:custom, __MODULE__, :validate_optional_string, []},
              default: nil,
              doc: """
              The base config this one builds on (only `"default"`, i.e. `kgs/_default.exs`).
              The merge is per-section, the way Tablassert's table configs override sections:
              `slots` merge per slot name (an override replaces one slot, inherits the rest),
              `aliases` append, and every other key — templates, evidence, known_errors — is
              replaced wholesale when present, inherited when not.
              """
            ],
            url: [type: :string, doc: "Link to the knowledge source's own documentation."],
            description: [
              type: :string,
              doc: "Prose about the KP, shown in the edge page's about section."
            ],
            feedback_repo: [
              type: :string,
              doc: "GitHub repo whose issue tracker takes corrections for this KG."
            ],
            aliases: [
              type: {:list, :map},
              default: [],
              doc: """
              Alternative spellings for a field, tried in order after the canonical name:
              `%{field: "regulatory_approvals", as: "FDA_regulatory_approvals"}`. Add
              `versions: "<1.16.0"` to restrict one to a version range.

              Needed because real DAKP releases rename fields — and not cleanly: the same version
              can appear in two dumps with different spellings. So these are fallbacks, not
              replacements: the canonical key wins when present, and a config that names only the
              current spelling still renders older documents.
              """
            ],
            slots: [
              type: {:map, :string, :any},
              default: %{},
              doc: "Named value specs a template can reference as `{name}`."
            ],
            title: [type: :any, doc: "Short label for the page title and link previews."],
            edge: [type: :any, required: true, doc: "The sentence describing this relationship."],
            asserts: [
              type: :any,
              doc: """
              The lead-in line above the sentence naming who makes the claim (a value spec,
              often just "{kp}"). Rendered as its own paragraph ending in a colon, so the
              sentence itself reads as the claim alone.
              """
            ],
            known_errors: [
              type: {:list, :map},
              default: [],
              doc: """
              Known defects, each `%{description: text, versions: [requirement]}`: one issue
              stated once, then every release it occurs in. Each entry of `versions` is a
              version requirement in the `{:version, ...}` syntax (a bare `"1.16.0"` matches
              exactly, `"<1.17.0"` a range); `description` is free text stating what is
              wrong, shown verbatim on a notice when any of those releases is displayed.
              """
            ],
            methods: [type: :any, doc: "How the association was generated and its sources."],
            evidence: [
              type: {:list, :map},
              default: [],
              doc: """
              Evidence sentences, each `%{value: spec}` with optional `:if` conditions. Every
              sentence that passes its conditions and renders non-empty becomes part of one
              prose paragraph on the edge page — the legacy page printed the same facts as
              labelled lines; prose carries each fact's meaning without a field-name label.
              """
            ],
            footnote: [
              type: :any,
              doc: """
              An optional trailing sentence rendered as its own paragraph beneath the evidence
              paragraph — for helper text a reader acts on ("On DailyMed, search ...") rather
              than a fact about the edge, which would dilute the evidence paragraph's argument.
              """
            ]
          )

  # NimbleOptions has no nullable string type, and it validates defaults too; display_name and
  # extends are both omit-able strings.
  @doc false
  def validate_optional_string(value) when is_binary(value) or value == nil,
    do: {:ok, value}

  def validate_optional_string(value),
    do: {:error, "expected a string or nil, got: #{inspect(value)}"}

  defstruct [
    :name,
    :display_name,
    :url,
    :description,
    :feedback_repo,
    :aliases,
    :slots,
    :title,
    :edge,
    :asserts,
    :known_errors,
    :methods,
    :evidence,
    :footnote,
    :file
  ]

  @type t :: %__MODULE__{}

  @doc "The NimbleOptions schema, for docs and for `mix linkouts.check`."
  def schema, do: @schema

  @doc """
  Validates a raw config map and returns a struct.

  Returns `{:error, problems}` where `problems` is a list of human-readable strings, so a bad
  config reports every mistake at once instead of one per rebuild.
  """
  @spec new(map(), keyword()) :: {:ok, t()} | {:error, [String.t()]}
  def new(raw, opts \\ []) when is_map(raw) do
    file = Keyword.get(opts, :file, "<unknown>")

    with {:ok, validated} <- validate_schema(raw),
         :ok <- validate_aliases(validated[:aliases], file),
         :ok <- validate_slots(validated[:slots], file),
         :ok <- validate_spec(validated[:edge], "edge", file),
         :ok <- validate_optional_spec(validated[:title], "title", file),
         :ok <- validate_optional_spec(validated[:asserts], "asserts", file),
         :ok <- validate_optional_spec(validated[:methods], "methods", file),
         :ok <- validate_known_errors(validated[:known_errors] || [], file),
         :ok <- validate_evidence(validated[:evidence], file),
         :ok <- validate_optional_spec(validated[:footnote], "footnote", file) do
      {:ok, struct(__MODULE__, Map.put(validated, :file, file))}
    else
      {:error, %NimbleOptions.ValidationError{} = err} ->
        {:error, ["#{file}: #{Exception.message(err)}"]}

      {:error, problems} ->
        {:error, problems}
    end
  end

  defp validate_schema(raw) do
    case NimbleOptions.validate(raw |> Map.new(fn {k, v} -> {to_key(k), v} end), @schema) do
      {:ok, validated} -> {:ok, validated}
      {:error, err} -> {:error, err}
    end
  end

  defp to_key(k) when is_atom(k), do: k
  defp to_key(k) when is_binary(k), do: String.to_atom(k)

  # --- grammar validation ---------------------------------------------------------------

  defp validate_slots(slots, file) do
    problems =
      for {name, spec} <- slots,
          problem <- spec_problems(spec, "slots.#{name}"),
          do: "#{file}: #{problem}"

    if problems == [], do: :ok, else: {:error, problems}
  end

  defp validate_spec(spec, where, file) do
    case spec_problems(spec, where) do
      [] -> :ok
      problems -> {:error, Enum.map(problems, &"#{file}: #{&1}")}
    end
  end

  defp validate_optional_spec(nil, _where, _file), do: :ok
  defp validate_optional_spec(spec, where, file), do: validate_spec(spec, where, file)

  defp validate_evidence(sections, file) do
    problems =
      for {%{} = section, index} <- Enum.with_index(sections),
          problem <- section_problems(section, "evidence[#{index}]"),
          do: "#{file}: #{problem}"

    if problems == [], do: :ok, else: {:error, problems}
  end

  # One known issue names a defect once and lists every release where it occurs. A bad
  # version requirement must fail here rather than silently matching nothing; an issue with
  # no versions would match nothing, so it is a config bug too.
  defp validate_known_errors(errors, file) do
    problems =
      for {%{} = error, index} <- Enum.with_index(errors),
          problem <- error_problems(error, "known_errors[#{index}]"),
          do: "#{file}: #{problem}"

    if problems == [], do: :ok, else: {:error, problems}
  end

  defp error_problems(error, where) do
    description = Map.get(error, :description)
    versions = Map.get(error, :versions)

    []
    |> Kernel.++(
      if is_binary(description) and description != "",
        do: [],
        else: ["#{where}: missing :description"]
    )
    |> Kernel.++(versions_problems(versions, where))
  end

  defp versions_problems(versions, where) when is_list(versions) do
    if versions == [] do
      ["#{where}: :versions must not be empty"]
    else
      for {version, index} <- Enum.with_index(versions),
          problem <- version_requirement_problems(version, "#{where}.versions[#{index}]"),
          do: problem
    end
  end

  defp versions_problems(_other, where), do: ["#{where}: missing :versions"]

  defp version_requirement_problems(version, where)
       when is_binary(version) and version != "",
       do: check_requirement(version, where)

  defp version_requirement_problems(_version, where),
    do: ["#{where}: version requirement must be a non-empty binary"]

  defp check_requirement(version, where) when is_binary(version) and version != "" do
    _ = Version.satisfies?("1.0.0", version)
    []
  rescue
    e in ArgumentError -> ["#{where}: #{Exception.message(e)}"]
  end

  defp check_requirement(_, _where), do: []

  defp section_problems(section, where) do
    # Each section is one sentence of the evidence paragraph; a sentence must render something.
    required =
      if Map.has_key?(section, :value) do
        []
      else
        ["#{where}: missing required key :value"]
      end

    value =
      spec_problems(Map.get(section, :value), "#{where}.value")
      |> Enum.reject(&(&1 =~ "nil spec"))

    conditions = condition_problems(Map.get(section, :if, []), "#{where}.if")
    required ++ value ++ conditions
  end

  @doc false
  # Every problem in a spec, depth-first. Slot references cannot be checked here (a slot may be
  # defined in the same config), so Display.check_slots/1 does that pass once the whole config is
  # loaded.
  def spec_problems(nil, _where), do: ["nil spec"]
  def spec_problems(spec, _where) when is_binary(spec), do: []

  def spec_problems({:field, name}, _where) when is_binary(name) or is_atom(name), do: []

  def spec_problems({:field, other}, where),
    do: ["#{where}: {:field, _} needs a name, got #{inspect(other)}"]

  def spec_problems({:link, label, curie}, _where)
      when (is_binary(label) or is_atom(label)) and (is_binary(curie) or is_atom(curie)), do: []

  def spec_problems({:link, _, _} = other, where),
    do: ["#{where}: {:link, label_field, curie_field} expected, got #{inspect(other)}"]

  def spec_problems({:preview, name, inner, separator, max}, where)
      when is_integer(max) and max > 0 do
    list_problems(name, inner, separator, max, where, :preview)
  end

  def spec_problems({:preview, _, _, _, _} = other, where) do
    [
      "#{where}: {:preview, field, inner, separator, max} expected, got #{inspect(other)}"
    ]
  end

  def spec_problems({:list, name, inner, separator}, where) do
    list_problems(name, inner, separator, nil, where)
  end

  def spec_problems({:list, name, inner, separator, max}, where)
      when is_integer(max) and max > 0 do
    list_problems(name, inner, separator, max, where)
  end

  def spec_problems({:list, name, inner, separator, max, conjunction}, where)
      when is_integer(max) and max > 0 and is_binary(conjunction) do
    list_problems(name, inner, separator, max, where)
  end

  def spec_problems({:list, _, _, _, _, _} = other, where) do
    [
      "#{where}: {:list, field, inner, separator, max, conjunction} expected, got #{inspect(other)}"
    ]
  end

  def spec_problems({:list, _, _, _, _} = other, where) do
    [
      "#{where}: {:list, field, inner, separator} or {:list, field, inner, separator, max} expected, got #{inspect(other)}"
    ]
  end

  def spec_problems({:list, other}, where),
    do: ["#{where}: {:list, name, inner, separator} expected, got #{inspect(other)}"]

  def spec_problems({:fold, label, inner}, where) when is_binary(label) do
    spec_problems(inner, "#{where}.inner")
  end

  def spec_problems({:fold, _, _} = other, where),
    do: ["#{where}: {:fold, \"label\", inner} expected, got #{inspect(other)}"]

  def spec_problems({:strong, inner}, _where) when is_binary(inner), do: []

  # A nested spec (a bolded link, a bolded fold): recurse into it like {:fold} does.
  def spec_problems({:strong, inner}, where) when is_tuple(inner) and tuple_size(inner) >= 2,
    do: spec_problems(inner, "#{where}.inner")

  def spec_problems({:strong, other}, where),
    do: ["#{where}: {:strong, inner} expected, got #{inspect(other)}"]

  def spec_problems({:pick, name, branches}, where) when is_map(branches) do
    name_problems =
      if is_binary(name) or is_atom(name),
        do: [],
        else: ["#{where}: {:pick, ...} needs a field name"]

    branch_problems =
      for {key, spec} <- branches,
          problem <- spec_problems(spec, "#{where}.#{inspect(key)}"),
          do: problem

    has_default =
      if Map.has_key?(branches, :default),
        do: [],
        else: [
          "#{where}: {:pick, #{name}, ...} has no :default branch, so an unlisted value renders nothing"
        ]

    name_problems ++ branch_problems ++ has_default
  end

  def spec_problems({:pick, _, _} = other, where),
    do: ["#{where}: {:pick, name, branches_map} expected, got #{inspect(other)}"]

  def spec_problems({:number, name, format}, _where)
      when (is_binary(name) or is_atom(name)) and format in [:sig2, :g4, :int], do: []

  def spec_problems({:number, _, _} = other, where),
    do: ["#{where}: {:number, field, :sig2 | :g4 | :int} expected, got #{inspect(other)}"]

  def spec_problems({tag, name}, _where)
      when tag in [:humanize, :count, :local, :names] and (is_binary(name) or is_atom(name)),
      do: []

  def spec_problems({tag, _} = other, where) when tag in [:humanize, :count, :local, :names],
    do: ["#{where}: {#{inspect(tag)}, field} expected, got #{inspect(other)}"]

  def spec_problems({:default, name, fallback}, where) when is_binary(name) or is_atom(name) do
    spec_problems(fallback, "#{where}.fallback")
  end

  def spec_problems({:default, _, _} = other, where),
    do: ["#{where}: {:default, field, fallback} expected, got #{inspect(other)}"]

  def spec_problems({:compound, clauses}, where) when is_list(clauses),
    do: Enum.flat_map(clauses, &spec_problems(&1, "#{where}.clause"))

  def spec_problems({:compound, _}, where), do: ["#{where}: compound clauses must be a list"]

  def spec_problems({:names, name, prefix, suffix}, where)
      when is_binary(name) or is_atom(name),
      do: spec_problems(prefix, "#{where}.prefix") ++ spec_problems(suffix, "#{where}.suffix")

  def spec_problems({:names, _, _, _} = other, where),
    do: ["#{where}: {:names, field, prefix, suffix} expected, got #{inspect(other)}"]

  def spec_problems({:or_query, name, original_name, %{variants: variants}}, where)
      when (is_binary(name) or is_atom(name)) and
             (is_binary(original_name) or is_atom(original_name)) and is_list(variants) do
    bad = Enum.reject(variants, &(&1 in [:phrase, :terms]))

    cond do
      variants == [] ->
        ["#{where}: {:or_query, ..., %{variants: [...]}} variants must be non-empty"]

      bad != [] ->
        [
          "#{where}: {:or_query, ..., %{variants: [...]}} variants must be :phrase/:terms, got #{inspect(bad)}"
        ]

      true ->
        []
    end
  end

  def spec_problems({:or_query, _, _, other}, where),
    do: [
      "#{where}: {:or_query, field, original_field, %{variants: [...]}} expected, got #{inspect(other)}"
    ]

  def spec_problems({:or_query, name, original_name}, _where)
      when (is_binary(name) or is_atom(name)) and
             (is_binary(original_name) or is_atom(original_name)),
      do: []

  def spec_problems({:or_query, _, _} = other, where),
    do: ["#{where}: {:or_query, field, original_field} expected, got #{inspect(other)}"]

  def spec_problems({:supporting, key, prefix, suffix, rewordings, fallback}, where)
      when is_binary(key) and is_binary(prefix) and is_binary(suffix) and is_map(rewordings) do
    spec_problems(fallback, "#{where}.fallback")
  end

  def spec_problems({:supporting, _, _, _, _, _} = other, where),
    do: [
      "#{where}: {:supporting, key, prefix, suffix, rewordings, fallback} expected, got #{inspect(other)}"
    ]

  def spec_problems({:url, template, label}, where) when is_binary(template) do
    spec_problems(label, "#{where}.label")
  end

  def spec_problems({:url, _, _} = other, where),
    do: ["#{where}: {:url, \"template\", label} expected, got #{inspect(other)}"]

  def spec_problems({:if, conditions, then_spec}, where) do
    spec_problems({:if, conditions, then_spec, nil}, where)
  end

  def spec_problems({:if, conditions, then_spec, else_spec}, where) do
    condition_problems(conditions, "#{where}.if") ++
      spec_problems(then_spec, "#{where}.then") ++
      case else_spec do
        nil -> []
        spec -> spec_problems(spec, "#{where}.else")
      end
  end

  def spec_problems(other, where), do: ["#{where}: unsupported spec #{inspect(other)}"]

  # Shared checks for list and preview forms; :max controls folding or the display bound.
  defp list_problems(name, inner, separator, _max, where, tag \\ :list) do
    cond do
      not (is_binary(name) or is_atom(name)) -> ["#{where}: {:#{tag}, ...} needs a field name"]
      not is_binary(separator) -> ["#{where}: {:#{tag}, ...} separator must be a string"]
      true -> spec_problems(inner, "#{where}.inner")
    end
  end

  defp condition_problems(conditions, where) when is_list(conditions) do
    Enum.flat_map(conditions, &condition_problem(&1, where))
  end

  defp condition_problems(other, where),
    do: ["#{where}: conditions must be a list, got #{inspect(other)}"]

  defp condition_problem({:present, name}, _where) when is_binary(name) or is_atom(name), do: []

  defp condition_problem({:present, other}, where),
    do: ["#{where}: {:present, name} expected, got #{inspect(other)}"]

  defp condition_problem({:eq, name, _value}, _where) when is_binary(name) or is_atom(name),
    do: []

  defp condition_problem({:eq, _, _} = other, where),
    do: ["#{where}: {:eq, name, value} expected, got #{inspect(other)}"]

  defp condition_problem({:matches, name, pattern}, _where)
       when (is_binary(name) or is_atom(name)) and is_binary(pattern), do: []

  defp condition_problem({:matches, _, _} = other, where),
    do: ["#{where}: {:matches, name, \"regex\"} expected, got #{inspect(other)}"]

  # Parsed eagerly so a bad requirement fails `mix linkouts.check` instead of raising on the first
  # page view that evaluates it. Implicit try: the rescue belongs to this function, not a block.
  defp condition_problem({:version, requirement}, where) when is_binary(requirement) do
    Version.satisfies?("0.0.1", requirement)
    []
  rescue
    ArgumentError -> ["#{where}: bad version requirement #{inspect(requirement)}"]
  end

  defp condition_problem({:version, other}, where),
    do: ["#{where}: {:version, \"<op><version>\"} expected, got #{inspect(other)}"]

  # Numeric comparisons, for direction words driven by a coefficient's sign.
  defp condition_problem({tag, name, bound}, _where)
       when tag in [:lt, :gt, :lte, :gte] and (is_binary(name) or is_atom(name)) and
              is_number(bound),
       do: []

  defp condition_problem({tag, _, _} = other, where) when tag in [:lt, :gt, :lte, :gte],
    do: ["#{where}: {#{inspect(tag)}, field, number} expected, got #{inspect(other)}"]

  # Length of a list field, used for pluralization ("a clinical trial" vs "3 clinical trials").
  defp condition_problem({:count_gt, name, bound}, _where)
       when (is_binary(name) or is_atom(name)) and is_integer(bound),
       do: []

  defp condition_problem({:count_gt, _, _} = other, where),
    do: ["#{where}: {:count_gt, field, integer} expected, got #{inspect(other)}"]

  defp condition_problem({op, conditions}, where)
       when op in [:all, :any] and is_list(conditions) do
    Enum.flat_map(conditions, &condition_problem(&1, "#{where}.#{op}"))
  end

  defp condition_problem(other, where), do: ["#{where}: unsupported condition #{inspect(other)}"]

  defp validate_aliases(aliases, file) do
    problems =
      Enum.flat_map(aliases, fn
        %{field: field, as: as} = rule when is_binary(field) and is_binary(as) ->
          case Map.get(rule, :versions) do
            nil ->
              []

            versions when is_binary(versions) ->
              requirement_problems(file, field, versions)

            other ->
              [
                "#{file}: alias #{field} :versions must be a string requirement, got #{inspect(other)}"
              ]
          end

        %{field: field} ->
          ["#{file}: alias #{inspect(field)} needs an :as key naming the alternative spelling"]

        other ->
          ["#{file}: alias must be a map with :field and :as, got #{inspect(other)}"]
      end)

    if problems == [], do: :ok, else: {:error, problems}
  end

  @doc """
  The candidate keys for each canonical field name at a specific version.

  Returns `%{canonical => [canonical, alias, ...]}` in lookup order, with aliases whose version
  requirement the key does not satisfy dropped. `Value.fetch/2` walks the list and takes the first
  key present in the document, so a doc that uses the canonical spelling is never shadowed by an
  older one, and a doc that only has the old spelling still renders.

  Version-scoped and unscoped aliases for one field may coexist; the scoped ones come first
  because they carry more information about which spelling that release meant.
  """
  @spec aliases_for(t(), String.t()) :: %{String.t() => [String.t()]}
  def aliases_for(%__MODULE__{aliases: aliases}, version_key) do
    matching =
      for %{field: field, as: as} = alias_ <- aliases,
          matches_version?(Map.get(alias_, :versions), version_key) do
        {field, as, Map.get(alias_, :versions) != nil}
      end

    scoped = for {field, as, true} <- matching, do: {field, as}
    unscoped = for {field, as, false} <- matching, do: {field, as}

    Enum.reduce(scoped ++ unscoped, %{}, fn {field, as}, acc ->
      Map.update(acc, field, [field, as], fn candidates ->
        if as in candidates, do: candidates, else: candidates ++ [as]
      end)
    end)
  end

  # Parsed eagerly so a bad requirement fails `mix linkouts.check` instead of raising on the
  # first page view that evaluates it.
  defp requirement_problems(file, field, requirement) do
    Version.satisfies?("0.0.1", requirement)
    []
  rescue
    ArgumentError ->
      ["#{file}: alias #{field} has a bad version requirement #{inspect(requirement)}"]
  end

  defp matches_version?(nil, _version_key), do: true

  defp matches_version?(requirement, version_key),
    do: Version.satisfies?(version_key, requirement)

  @doc """
  Slot names a template references but the config does not define.

  Not an error on its own, because `{name}` legitimately falls back to a document field. Kept
  public so the check task and tests can reason about it.
  """
  @spec undefined_slots(t()) :: [String.t()]
  def undefined_slots(%__MODULE__{} = config) do
    defined = Map.keys(config.slots)

    referenced =
      referenced_slots(config.edge) ++
        referenced_slots(config.title) ++
        referenced_slots(config.asserts) ++
        referenced_slots(config.methods) ++
        referenced_slots(config.footnote)

    referenced =
      referenced ++
        Enum.flat_map(config.evidence, &referenced_slots(Map.get(&1, :value))) ++
        Enum.flat_map(Map.values(config.slots), &collect_spec_slots/1)

    # uniq/1 before --: list subtraction is multiset subtraction, so a slot referenced by two
    # branches would survive one removal and be reported as missing.
    (Enum.uniq(referenced) -- defined) |> Enum.sort()
  end

  defp referenced_slots(nil), do: []
  defp referenced_slots(spec) when is_binary(spec), do: collect_spec_slots(spec)
  defp referenced_slots(spec), do: collect_spec_slots(spec)

  defp collect_spec_slots(nil), do: []

  defp collect_spec_slots(spec) when is_binary(spec) do
    Regex.scan(~r/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/, spec, capture: :all_but_first) |> List.flatten()
  end

  defp collect_spec_slots({:list, _name, inner, _sep}), do: collect_spec_slots(inner)
  defp collect_spec_slots({:list, _name, inner, _sep, _max}), do: collect_spec_slots(inner)
  defp collect_spec_slots({:list, _name, inner, _sep, _max, _conj}), do: collect_spec_slots(inner)
  defp collect_spec_slots({:preview, _name, inner, _sep, _max}), do: collect_spec_slots(inner)

  defp collect_spec_slots({:pick, _name, branches}) when is_map(branches) do
    Enum.flat_map(Map.values(branches), &collect_spec_slots/1)
  end

  defp collect_spec_slots({:if, _conditions, then_spec, else_spec}) do
    collect_spec_slots(then_spec) ++ collect_spec_slots(else_spec)
  end

  defp collect_spec_slots({:compound, clauses}), do: Enum.flat_map(clauses, &collect_spec_slots/1)

  defp collect_spec_slots({:names, _name, prefix, suffix}),
    do: collect_spec_slots(prefix) ++ collect_spec_slots(suffix)

  defp collect_spec_slots({:default, _name, fallback}), do: collect_spec_slots(fallback)
  # An OR query reads document fields only; it references no slots.
  defp collect_spec_slots({:or_query, _name, _original_name}), do: []
  defp collect_spec_slots({:or_query, _name, _original_name, _opts}), do: []
  # A {:supporting} clause's framing is literal text; only its fallback can reference slots.
  defp collect_spec_slots({:supporting, _key, _prefix, _suffix, fallback}),
    do: collect_spec_slots(fallback)

  # A fold's inner spec resolves against the same document as the sentence around it.
  defp collect_spec_slots({:fold, _label, inner}), do: collect_spec_slots(inner)
  defp collect_spec_slots({:strong, inner}), do: collect_spec_slots(inner)
  # The url template interpolates slots-then-fields just like a string template, so both count.
  defp collect_spec_slots({:url, template, label}),
    do: collect_spec_slots(template) ++ collect_spec_slots(label)

  defp collect_spec_slots({:if, _conditions, then_spec}) do
    collect_spec_slots(then_spec)
  end

  defp collect_spec_slots(_other), do: []
end
