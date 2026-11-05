# The two-node proofs start distribution and take seconds; they run explicitly via
# `mix test --include cluster`.
ExUnit.start(exclude: [:cluster, :live])
