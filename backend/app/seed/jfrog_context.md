# JFrog context pack (as of Oct 2026)

Sources: JFrog FY2025 Form 10-K (filed Feb 2026), Q2 2026 results press release (Aug 6, 2026), JFrog press room and blog (2025-2026), and the jfrog.com product pages in the JFrog knowledge base (crawled Oct 2026); as of Oct 2026. Treat anything newer in a document as more current.

## Positioning
JFrog (NASDAQ: FROG) is the software supply chain platform, positioning itself as the "system of record for the software supply chain in the AI era". The pitch: one place where every binary, package, container image, AI model and release artifact is stored, secured, governed and distributed from code to runtime, across hybrid and multi-cloud environments. Customers are mostly large enterprises with heavy regulatory and security needs. The swampUP 2026 conference theme was "Trusted Intelligence" and a "Trust Layer" for software and AI.

## Product portfolio
- Artifactory: universal artifact repository and the core of the platform; 60+ artifact types (packages, files and AI assets), remote repositories that proxy public registries and AI model hubs, virtual repositories, multi-site sync and promotion across environments.
- Container Registry: private registry for Docker container images and Helm charts, part of Artifactory, deployable on-prem or in the cloud.
- Xray: software composition analysis; continuously scans repositories, builds and container images for open-source vulnerabilities and license compliance, with remediation in the IDE and CLI.
- Advanced Security: contextual analysis that checks whether a vulnerability is actually reachable or exploitable in the application, plus secrets detection and infrastructure-as-code scanning.
- Curation: vets open-source packages, AI models and IDE extensions at the point of request and blocks risky ones before they reach developers (a "dependency firewall"), serving the next policy-compliant version instead; JFrog's research team flags malicious packages (typosquatting, maintainer hijacking), often before public databases.
- Distribution: secure, fast delivery of release bundles to remote sites, clouds, edges, embedded devices and IoT fleets, going beyond a traditional CDN or P2P.
- Connect: IoT device management on the JFrog Platform: over-the-air updates (commands, scripts, artifacts, files or containers) to device fleets, fleet grouping and continuous monitoring, from developer to device.
- Runtime: security for what is running in production, tracing runtime findings back to the artifact.
- AppTrust: release governance: policy gates across the SDLC that block non-compliant releases, using evidence of security, quality and compliance, for human- and AI-written software.
- JFrog ML: MLOps platform to build, deploy, manage and monitor AI workflows, from GenAI and LLM applications to classic ML.
- AI Catalog: one registry of approved AI assets, stored as artifacts in Artifactory; it exposes shadow AI, checks AI assets for vulnerabilities and malicious code, and blocks risky ones before they are pulled.
- MCP Registry: governed registry of pre-vetted MCP servers, with access control down to the MCP tool level, integrated with Curation to block malicious or unverified servers.
- Platform: the end-to-end JFrog Platform, the single source of truth for every software package, ML model and agent asset; enterprise setups offer a 99.99% uptime SLA.
- Pipelines (CI/CD) reached end of life on May 1, 2026; JFrog now leans on integrations with GitHub and other CI systems instead.
- Deployment options: fully managed SaaS on AWS, Azure and Google Cloud, self-managed, hybrid, multi-cloud and air-gapped; self-managed and SaaS sites can federate.

## Financial snapshot (Q2 2026)
- Revenue $163.8M, up 29% year over year.
- Cloud revenue $87.5M, up 53% year over year; cloud is the main growth engine and the shift from self-managed to SaaS continues.
- Net dollar retention 121%.
- 97 customers with more than $1M in annual recurring revenue.
Growth is driven by cloud migration, security attach (Xray, Advanced Security, Curation) and AI-related demand for governing models and packages.

## Partners and ecosystem
- GitHub: named JFrog's 2025 Technology Partner of the Year; deep integration, yet GitHub also sells its own packages and registry, so the relationship is partly competitive.
- NVIDIA: integration with NVIDIA NIM so optimized AI model containers can be governed and delivered through JFrog.
- Wiz: partnership announced September 2026, linking cloud security findings with artifact and supply chain context.
- Chainguard: remediation partner for hardened, minimal container images; also a rival in secure base images.
- Hugging Face: model scanning partnership, so downloaded models are checked before use.

## Main competitive set
- Direct artifact and registry rivals: Sonatype, Cloudsmith, GitLab package and Artifact Central registries, Harness Artifact Registry, and the cloud-provider registries (AWS CodeArtifact and ECR, Azure Artifacts and ACR, Google Artifact Registry).
- Supply chain security rivals: Snyk, Black Duck, Socket, Endor Labs, Checkmarx, Mend, Aqua, Wiz and Palo Alto Networks.
- Frenemies: GitHub and Chainguard, which are both partners and competitors.

## What matters most to JFrog
Anything that affects: cloud (SaaS) adoption and pricing, security attach rates, AI model and MCP governance, software supply chain attacks that raise demand for curation and scanning, bundling moves by hyperscalers and GitHub or GitLab, hardened-image and remediation offerings, regulation such as the EU Cyber Resilience Act and SBOM requirements, and new AI-native developer workflows that change how packages and models are consumed.

## Sources
- https://www.sec.gov/Archives/edgar/data/1800667/000119312526051382/frog-20251231.htm (FY2025 10-K)
- https://jfrog.com/press-room/jfrog-announces-second-quarter-2026-results/ (Q2 2026 results)
- https://jfrog.com/blog/jfrog-github-2025-technology-partner-award/ (GitHub 2025 Technology Partner of the Year)
- https://jfrog.com/press-room/jfrog-partners-with-wiz-to-close-the-gap-on-ai-era-threats-keeping-global-businesses-secure/ (Wiz partnership, Sep 2026)
- https://docs.jfrog.com/releases/docs/pipeline-deprecation-end-of-life (Pipelines EOL May 1, 2026)
- https://jfrog.com/blog/swampup-2026-preview-trust-layer-agentic-software-supply-chain/ (swampUP 2026)
