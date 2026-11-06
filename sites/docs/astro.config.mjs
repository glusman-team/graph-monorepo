import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://glusman-team.github.io",
  base: "/graph-monorepo",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "Graph Monorepo",
      disable404Route: true,
      description: "Build, publish, inspect, and operate multiomics knowledge graphs.",
      logo: {
        src: "./src/assets/graph.svg",
        alt: "Graph Monorepo",
      },
      favicon: "/favicon.svg",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/glusman-team/graph-monorepo",
        },
      ],
      editLink: {
        baseUrl: "https://github.com/glusman-team/graph-monorepo/edit/main/sites/docs/",
      },
      lastUpdated: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      sidebar: [
        {
          label: "Start here",
          items: [
            { label: "Choose your task", slug: "index" },
            { label: "Repository architecture", slug: "architecture" },
            { label: "Shared contracts", slug: "contracts" },
            { label: "Contributing", slug: "contributing" },
          ],
        },
        {
          label: "DAKP",
          items: [
            { label: "Dataset and sources", slug: "dakp" },
            { label: "Run the pipeline", slug: "dakp/run" },
            { label: "Automated releases", slug: "dakp/release-automation" },
          ],
        },
        {
          label: "Linkouts",
          items: [
            { label: "Overview and local preview", slug: "linkouts" },
            {
              label: "Ingest and operate",
              items: [{ autogenerate: { directory: "linkouts/operations" } }],
            },
            {
              label: "Display configuration",
              items: [{ autogenerate: { directory: "linkouts/display" } }],
            },
            { label: "Storage format", slug: "linkouts/storage-format" },
            { label: "Deployment", slug: "linkouts/deployment" },
            {
              label: "Architecture decisions",
              items: [{ autogenerate: { directory: "linkouts/adrs" } }],
            },
          ],
        },
      ],
      customCss: ["./src/styles/custom.css"],
    }),
  ],
});
