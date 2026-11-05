defmodule Mix.Tasks.Linkouts.Check do
  @moduledoc """
  Validates every KG display config in `kgs/`.

      mix linkouts.check

  Fails with a non-zero exit and one line per problem. Run it after editing a config: it catches
  the mistakes that would otherwise show up as a blank sentence on a page — a misspelled field, a
  `{slot}` referenced but never defined, a `{:pick, ...}` with no `:default` branch, an unparseable
  version requirement.

  The same checks run when the app compiles (`EdgeLinkouts.Display` loads and validates every
  config), so this task exists for a fast loop and for CI, where a clear one-line-per-problem
  report beats a compilation stack trace.
  """

  use Mix.Task

  alias EdgeLinkouts.{Codec, Display}

  @shortdoc "Validates kgs/*.exs display configs"

  @impl Mix.Task
  def run(_args) do
    Mix.Task.run("compile")

    problems = EdgeLinkouts.Display.check_all()
    warnings = field_warnings()
    loaded = EdgeLinkouts.Display.loaded_files()

    Enum.each(loaded, fn {name, file} ->
      Mix.shell().info("  #{name}  #{file}")
    end)

    cond do
      loaded == [] ->
        Mix.shell().error("no KG display configs found in kgs/")
        exit({:shutdown, 1})

      problems == [] ->
        Enum.each(warnings, &Mix.shell().info([:yellow, "warning: ", &1, :reset]))
        Mix.shell().info("#{length(loaded)} config(s) valid")

      true ->
        Enum.each(problems, &Mix.shell().error(&1))
        Mix.shell().error("#{length(problems)} problem(s) in #{length(loaded)} config(s)")
        exit({:shutdown, 1})
    end
  end

  # Cross-checks referenced field names against the committed contract fixtures. A name that is
  # neither a slot nor a field in any loaded document is almost certainly a typo; a name with no
  # fixture coverage is skipped, because guessing is worse than saying nothing.
  defp field_warnings do
    case observed_fields() do
      %{} = empty when map_size(empty) == 0 ->
        []

      observed ->
        for config <- EdgeLinkouts.Display.configs(),
            fields = Map.get(observed, config.name),
            not is_nil(fields),
            name <- EdgeLinkouts.Display.unresolved_names(config),
            not MapSet.member?(fields, name) do
          "#{config.file}: {#{name}} is not a slot and appears in no #{config.name} fixture document"
        end
    end
  end

  # %{kg_name => MapSet of field names} decoded from dagster/projects/linkouts/contracts/*.ndjson.
  defp observed_fields do
    dir = Path.expand("../../../../../dagster/projects/linkouts/contracts", __DIR__)

    dir
    |> Path.join("*.ndjson")
    |> Path.wildcard()
    |> Enum.reduce(%{}, &observe_file(&1, &2))
  end

  # Field names at the top level plus one level into lists of maps, because a config legitimately
  # reaches into a KGX `sources` entry with {:list, "sources", "{resource_id}", ", "} and that
  # nested name is just as real as a top-level one.
  defp field_names(doc) do
    nested =
      for value when is_list(value) <- Map.values(doc),
          element when is_map(element) <- value,
          do: Map.keys(element)

    MapSet.new(Map.keys(doc) ++ List.flatten(nested))
  end

  defp observe_file(path, acc) do
    path
    |> stored_lines()
    |> Enum.reduce(acc, &observe_line/2)
  end

  defp stored_lines(path) do
    path
    |> File.stream!()
    |> Stream.map(&String.trim/1)
    |> Stream.reject(&(&1 == ""))
    |> Enum.map(&JSON.decode!/1)
  rescue
    # A fixture that cannot be read is the contract test's problem, not this task's.
    _ -> []
  end

  defp observe_line(%{"b" => b64}, acc) do
    case Codec.decode(b64) do
      {:ok, blob} -> Enum.reduce(Codec.versions(blob), acc, &observe_version(&1, blob, &2))
      {:error, _} -> acc
    end
  end

  defp observe_line(_other, acc), do: acc

  defp observe_version(version, blob, acc) do
    with {:ok, doc} <- Codec.resolve(blob, version),
         name when not is_nil(name) <- Display.name_of(version) do
      Map.update(acc, name, field_names(doc), &MapSet.union(&1, field_names(doc)))
    else
      _ -> acc
    end
  end
end
