defmodule EdgeLinkouts.CodecTest do
  @moduledoc """
  The version-comparison rules both "latest" badge sites share. The home bar and the
  edge page's version timeline must agree on which release is newest, so the rule lives
  in one function they both call; these tests pin that function.
  """

  use ExUnit.Case, async: true

  alias EdgeLinkouts.Codec

  test "canonical numbers use shortest round-trip text without changing precision" do
    assert Codec.canonical_binary(1.25) == "1.25"
    assert Codec.canonical_binary(0.1) == "0.1"
    assert Codec.canonical_binary(1.0) == "1"
    assert Codec.canonical_binary(-0.0) == "0"
  end

  test "newest_label picks the numerically newest release regardless of input order" do
    assert Codec.newest_label(["1.11.2", "1.16.0"]) == "1.16.0"
    assert Codec.newest_label(["1.16.0", "1.23.5", "1.11.2"]) == "1.23.5"
  end

  test "newest_label compares numerically, not lexicographically" do
    assert Codec.newest_label(["1.9.0", "1.16.0"]) == "1.16.0"
  end

  test "a final release outranks its own pre-release" do
    assert Codec.newest_label(["1.16.0-rc.1", "1.16.0"]) == "1.16.0"
  end

  test "newest_label accepts full keys as well as bare versions" do
    assert Codec.newest_label([
             "infores:drugapprovals-kp-1.11.2",
             "infores:drugapprovals-kp-1.16.0"
           ]) ==
             "infores:drugapprovals-kp-1.16.0"
  end

  test "newest_label of an empty list is nil" do
    assert Codec.newest_label([]) == nil
  end
end
