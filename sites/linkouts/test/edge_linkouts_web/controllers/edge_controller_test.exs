defmodule EdgeLinkoutsWeb.EdgeControllerTest do
  use EdgeLinkoutsWeb.ConnCase, async: false

  alias EdgeLinkouts.Codec
  alias EdgeLinkouts.Cosmos
  alias EdgeLinkoutsWeb.Fixtures

  @v1 Fixtures.v1()
  @v2 Fixtures.v2()

  setup do
    Cosmos.Fake.reset()
    Cosmos.Fake.seed(Fixtures.docs())

    %{id: Fixtures.first_edge_id()}
  end

  test "downloads an attachment whose body is byte-identical to the canonical newest document", %{
    conn: conn,
    id: id
  } do
    conn = get(conn, "/edges/#{id}/download")

    assert response(conn, 200) == canonical_at(id, @v2)

    assert get_resp_header(conn, "content-type") == ["application/x-ndjson"]

    [disposition] = get_resp_header(conn, "content-disposition")
    # The version key's colon is illegal in a Windows filename, so the download name turns it
    # into a hyphen; the body and the ?version= parameter keep the canonical key.
    assert disposition ==
             ~s(attachment; filename="edge-#{id}-infores-drugapprovals-kp-1.16.0.ndjson")
  end

  test "?version selects that stored version", %{conn: conn, id: id} do
    conn = get(conn, "/edges/#{id}/download?version=#{@v1}")

    assert response(conn, 200) == canonical_at(id, @v1)
  end

  test "an unknown id answers HTTP 404", %{conn: conn} do
    conn = get(conn, "/edges/00000000-0000-0000-0000-000000000000/download")

    assert html_response(conn, 404) =~ "edge id was not found"
  end

  test "an unknown version answers HTTP 404", %{conn: conn, id: id} do
    conn = get(conn, "/edges/#{id}/download?version=nope-9.9.9")

    assert html_response(conn, 404) =~ "edge id was not found"
  end

  defp canonical_at(id, key) do
    Fixtures.edge_doc(id) |> Fixtures.resolved(key) |> Codec.canonical_binary()
  end

  test "the download is cacheable and repeat requests get a 304", %{conn: conn, id: id} do
    # Why: the body is a pure function of the stored document, so re-shipping it on every
    # repeat is wasted egress; the strong ETag turns repeats into header-only 304s.
    conn = get(conn, "/edges/#{id}/download")
    assert response(conn, 200)
    [etag] = get_resp_header(conn, "etag")
    assert String.starts_with?(etag, "\"") and String.ends_with?(etag, "\"")
    assert get_resp_header(conn, "cache-control") == ["public, max-age=300"]

    conn = build_conn() |> put_req_header("if-none-match", etag) |> get("/edges/#{id}/download")
    assert conn.status == 304
    assert conn.resp_body == ""
    assert get_resp_header(conn, "etag") == [etag]
  end

  test "different bodies produce different ETags", %{conn: conn, id: id} do
    conn = get(conn, "/edges/#{id}/download")
    [first_etag] = get_resp_header(conn, "etag")

    # The fixture's two versions of one edge can canonically equal; a second edge id is the
    # honest way to get a different body.
    [other_id] =
      Fixtures.edge_docs() |> Enum.map(& &1["id"]) |> Enum.reject(&(&1 == id)) |> Enum.take(1)

    conn = get(build_conn(), "/edges/#{other_id}/download")
    [other_etag] = get_resp_header(conn, "etag")

    refute other_etag == first_etag
  end
end
