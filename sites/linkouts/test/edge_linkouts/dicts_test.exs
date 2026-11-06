defmodule EdgeLinkouts.DictsTest do
  @moduledoc """
  The dictionary registry contract: ids come from the file's own header, a missing directory
  is an empty registry (dev without trained dicts), a malformed file or a duplicate id is a
  boot failure, and a document naming an id we do not carry fails loudly instead of decoding
  garbage with the wrong dictionary.
  """

  use ExUnit.Case, async: true

  alias EdgeLinkouts.Dicts

  @fixture_dir Path.expand("../../../../dagster/projects/linkouts/contracts", __DIR__)

  test "loads the contract fixture dictionary keyed by its header id" do
    registry = Dicts.load!(@fixture_dir)
    assert map_size(registry) == 1

    [path] = Path.wildcard(Path.join(@fixture_dir, "*.dict"))
    <<0xEC30A437::little-32, id::little-32, _::binary>> = File.read!(path)
    assert registry[id] == File.read!(path)
    assert id in 32_768..(Bitwise.bsl(1, 31) - 1)
  end

  test "a missing directory is an empty registry" do
    assert Dicts.load!(Path.join(@fixture_dir, "does-not-exist")) == %{}
  end

  test "for_doc resolves declared, absent, and unknown ids", %{test: _} do
    registry = Dicts.load!(@fixture_dir)
    [id] = Map.keys(registry)

    assert {:ok, nil} = Dicts.for_doc(%{}, registry)
    assert {:ok, nil} = Dicts.for_doc(%{"d" => 0}, registry)
    assert {:ok, bytes} = Dicts.for_doc(%{"d" => id}, registry)
    assert is_binary(bytes)
    assert {:error, {:unknown_dict, 42}} = Dicts.for_doc(%{"d" => 42}, registry)
  end

  test "a non-integer d field is a per-request error, not a crash" do
    for bad <- ["1997801878", 1.5, -1.0, [1], %{"id" => 1}, true] do
      assert {:error, {:bad_dict_field, ^bad}} = Dicts.for_doc(%{"d" => bad}, %{})
    end
  end

  test "a malformed dictionary raises at load, not at read time" do
    tmp = Path.join(System.tmp_dir!(), "dicts-bad-#{System.unique_integer([:positive])}")
    File.mkdir_p!(tmp)
    File.write!(Path.join(tmp, "junk.dict"), "not a dictionary")

    assert_raise ArgumentError, ~r/not a full zstd dictionary/, fn -> Dicts.load!(tmp) end
  end

  test "two dictionaries with the same id raise at load" do
    tmp = Path.join(System.tmp_dir!(), "dicts-dup-#{System.unique_integer([:positive])}")
    File.mkdir_p!(tmp)
    [path] = Path.wildcard(Path.join(@fixture_dir, "*.dict"))
    File.cp!(path, Path.join(tmp, "a.dict"))
    File.cp!(path, Path.join(tmp, "b.dict"))

    assert_raise ArgumentError, ~r/duplicate zstd dictionary id/, fn -> Dicts.load!(tmp) end
  end
end
