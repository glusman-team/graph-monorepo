defmodule EdgeLinkoutsWeb.EdgesTest do
  @moduledoc """
  Id validation on the read path.

  Why: every miss on an arbitrary id is a billed point read (request units are charged for
  404s too), and the id travels into a Cosmos resource URL. Ids that cannot exist must be
  answered locally - zero backend calls, zero request units - and the reserved `__`
  documents must never be fetchable as though they were edges. The Fake backend counts
  every call, so these tests prove the refusal happens before the store.
  """

  # Not async: the configured backend is the shared, module-named Fake instance that every
  # test reseeds, and the call-count assertions below need it undisturbed for their run.
  use ExUnit.Case, async: false

  alias EdgeLinkouts.Cosmos.Fake
  alias EdgeLinkoutsWeb.Edges

  setup do
    Fake.reset()
    on_exit(fn -> Fake.reset() end)
  end

  @bad_ids [
    "",
    "a/b",
    "a\\b",
    "a?b",
    "a#b",
    "__random_pool__",
    "__random_pool__:kg:1.0",
    String.duplicate("x", 256)
  ]

  test "ids that cannot be document ids answer not_found without reading" do
    for id <- @bad_ids do
      assert {:error, :not_found} = Edges.fetch_edge(id), "expected refusal for #{inspect(id)}"
    end

    assert Fake.calls() == []
  end

  test "a well-formed but unknown id still reads (and the read is what 404s)" do
    assert {:error, :not_found} = Edges.fetch_edge("00002f73-0cc8-304d-afc2-f5bfe9703daa")
    assert Fake.calls() == [{:get_edge, "00002f73-0cc8-304d-afc2-f5bfe9703daa"}]
  end

  test "valid_id? accepts the shapes the worker stores" do
    assert Edges.valid_id?("00002f73-0cc8-304d-afc2-f5bfe9703daa")
    assert Edges.valid_id?("CURIE-style_id.with~tilde+and=equals")
    refute Edges.valid_id?("")
  end
end
