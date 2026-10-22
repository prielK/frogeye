# Labels

Every label of the triage, disambiguation and retrieval sets, with its one-line reason. They were assigned blind from each document's raw text (title, URL, date, source and a 1,200-character excerpt), never from FrogEye's outputs: the export held no triage verdict, signal link or relevance flag.

**To change a label**, edit its row in the `.jsonl` file named in the section and re-run. Labels are never part of a model request, so nothing needs re-recording: `make eval` prints the new numbers (and exits 1, since they no longer match `results/latest.json`); `make eval-record` then rewrites `latest.*` at no cost when every call is already recorded (it needs `OPENAI_API_KEY` in `.env`, but makes no call).

- triage: `relevant` (true/false), `competitors` (the vendors it must name), `also_ok` (vendors it may also name).
- disambiguation: `expected` (the candidate's slug when the document is about that company, else `null`).
- retrieval: `relevant` (the refs a good search returns: `doc:<id>` corpus documents, `kb:<id>` JFrog KB pages).
- corpus (`corpus/documents.jsonl`): `competitors`, the vendors each document is about. They become the documents' competitor links, which the filtered retrieval queries and the agents' search tool filter on; a change there can make a few rerank or disambiguation calls miss, so run `make eval-record` (cents).

## Triage (`triage.jsonl`, 60 rows: 30 relevant, 30 not)

`kind`: positive / negative, or a hard case (`hard_*`): a word-sense trap, a same-name product, JFrog's own news, a headline-only item.

| id | doc | title | label | kind | reason |
|---|---|---|---|---|---|
| 01 | 85 | Introducing Chainguard Harness: Vulnerability hunting and r… | relevant [chainguard] | hard_positive | Chainguard launches a product it calls Harness; about Chainguard, not Harness Inc. |
| 02 | 5317 | GitHub package firewall in public preview | relevant [github] | positive | GitHub package firewall: blocks malicious packages at install time. |
| 03 | 23 | Dependency Firewall: Block risky packages before the build | relevant [gitlab] | positive | GitLab Dependency Firewall blocks risky packages before the build. |
| 04 | 2475 | Sonatype Unveils Nexus Repository Pro+ to Stop Malicious Co… | relevant [sonatype] | hard_positive | Headline-only press item: Nexus Repository Pro+ screens packages, models, MCP servers. |
| 05 | 711 | Harness acquires Augment Code to advance the Autonomous SDLC | relevant [harness] | hard_positive | Short item: Harness acquires Augment Code (an M&A move by a tier-1 competitor). |
| 06 | 2498 | Docker Hub security advisory: personal access tokens transf… | relevant [docker] | positive | Docker Hub security advisory: access tokens exposed in account conversions. |
| 07 | 5161 | GitLab Stock Tumbles 10% Overnight: OpenAI Overhang Adds Pr… | relevant [gitlab] | hard_positive | Headline only: GitLab stock falls on a weak outlook (competitor financials, not JFrog's). |
| 08 | 4743 | Case Study: GitHub Expands Malware Advisories Across Eight… | relevant [github] | positive | GitHub expands malware advisories to eight package ecosystems with OpenSSF data. |
| 09 | 5096 | The distroless security gap: Why bottom-up containers are t… | relevant [industry] (also ok: chainguard, docker) | positive | Industry: distroless/minimal OCI images and scanner visibility. |
| 10 | 5367 | OVHcloud CLI releases backdoored on GitHub to steal develop… | relevant [industry] (also ok: github) | positive | Industry: backdoored CLI releases on GitHub steal developer secrets (supply-chain attack). |
| 11 | 4432 | Mend.io upgrades 184 open source organizations to the Renov… | relevant [mend] | positive | Mend.io moves 184 OSS orgs to its Renovate Community plan. |
| 12 | 5319 | Introducing Harness AI Budgets | relevant [harness] | positive | Harness launches AI Budgets in its Cost Management Agent. |
| 13 | 2521 | Introducing Socket Scanning for VS Code Marketplace Extensi… | relevant [socket] | positive | Socket adds scanning for VS Code Marketplace extensions. |
| 14 | 5334 | Wiz Sensor on GKE. 1-Click Runtime Protection, Now Native. | relevant [wiz] | positive | Wiz runtime sensor ships as a native GKE add-on. |
| 15 | 81 | Introducing Chainguard Workspaces | relevant [chainguard] | positive | Chainguard Workspaces: hardened microVM environments for agents. |
| 16 | 44 | Cloudsmith expands support to Nix | relevant [cloudsmith] | positive | Cloudsmith adds Nix binary caches (a new package format). |
| 17 | 31 | Open source license compliance generally available | relevant [github] | positive | GitHub open source license compliance GA (dependency license policy). |
| 18 | 2510 | Purpose-built model for leaked secret detection | relevant [github] | positive | GitHub fine-tuned model for leaked secret detection. |
| 19 | 5118 | Buildkite Adds Scoped Secrets and New Agentic CI Tools | relevant [industry] (also ok: github) | hard_positive | Industry: CI provider Buildkite adds scoped secrets and MCP tools (CI/CD security). |
| 20 | 5128 | Dynamic MCP Server Discovery on Google Cloud | relevant [google-cloud] | hard_positive | Google Cloud MCP server registry/discovery (MCP registries are JFrog's AI market). |
| 21 | 4157 | AWS Agent Registry now supports custom metadata for registr… | relevant [aws] | hard_positive | AWS Agent Registry: governed catalog of agents and MCP servers. |
| 22 | 4640 | Announcing: Agent Harness in Microsoft Agent Framework | relevant [azure] | hard_positive | Microsoft's 'Agent Harness' runtime GA; Azure agent tooling, not Harness Inc. |
| 23 | 1097 | Introducing new Snyk AppRisk integrations: Enhancing applic… | relevant [snyk] | hard_positive | Snyk AppRisk integrations; Harness appears only as one integration. |
| 24 | 92 | Chainguard Actions is Generally Available: Hardening 1,000+… | relevant [chainguard] (also ok: github) | positive | Chainguard Actions GA: hardened GitHub Actions catalog. |
| 25 | 4326 | Extending Supply Chain Security to AI Models with Anchore E… | relevant [anchore] | positive | Anchore Enterprise 6.2 extends supply-chain scanning to AI models. |
| 26 | 5092 | Supply Chain Zero Days: Closing the Gap Between Publication… | relevant [wiz] | positive | Wiz detects compromised packages at publication (supply-chain zero days). |
| 27 | 4642 | The reality of using LLMs to identify open source risk: Hel… | relevant [black-duck] | positive | Black Duck on LLMs for software composition analysis. |
| 28 | 4700 | Hackers hijacked 1,900 GitHub accounts. Then they went afte… | relevant [industry] (also ok: github) | positive | Industry: GhostAction campaign hijacks GitHub accounts and CI secrets. |
| 29 | 5353 | New OpenSSF Guide: Finding and Fixing Vulnerabilities Using… | relevant [industry] | positive | Industry: OpenSSF guide to finding and fixing vulnerabilities with AI. |
| 30 | 2519 | TensorLake npm SDK Compromised in ChainDrop Shai-Hulud Cred… | relevant [socket] | positive | Socket detects the compromised TensorLake npm SDK (supply-chain attack). |
| 31 | 5218 | Harness Engineering 101: The LLM is the brain, the harness… | **not relevant** | hard_negative | 'Harness' here is an AI agent harness (a book on agent engineering), not Harness Inc. |
| 32 | 5423 | HAR 1.0: The open harness for AI coding workflows you can t… | **not relevant** | hard_negative | Open-source agent harness for coding workflows; word sense, generic AI tooling. |
| 33 | 5412 | Show HN: Harness Studio: an open-source desktop coding agen… | **not relevant** | hard_negative | 'Harness Studio' desktop coding agent; unrelated to Harness Inc. or the supply chain. |
| 34 | 5422 | Which companies build both their own model and their own ha… | **not relevant** | hard_negative | List of AI labs and their agent harnesses; word sense, no JFrog-market news. |
| 35 | 5221 | Harness²: Recursive Agent Harnessing for an Open World (Goo… | **not relevant** | hard_negative | Research paper on recursive agent harnesses; word sense. |
| 36 | 5135 | Show HN: KiviDB, a faster Redis-compatible memory database… | **not relevant** | hard_negative | A Redis-compatible database; Docker appears only in 'docker run'. |
| 37 | 5136 | OneMemory – self-hosted shared memory for all your AI agent… | **not relevant** | hard_negative | Self-hosted agent memory store; 'Docker one-liner' is an install method. |
| 38 | 5166 | Show HN: Open-source AAS digital twin studio with live PLC… | **not relevant** | hard_negative | Industrial digital-twin studio; Docker Compose only as install step. |
| 39 | 5306 | Self-hosted AI spend attribution | **not relevant** | hard_negative | AI spend attribution tool; runs on Docker Compose (generic mention). |
| 40 | 5197 | Prism – a fast, open-source IDM alternative for Windows and… | **not relevant** | hard_negative | Download manager; 'socket' means network sockets, not Socket. |
| 41 | 4899 | JFrog sets third-quarter results date. UBS targets JFrog st… | **not relevant** | hard_negative | JFrog's own earnings date and analyst target (JFrog itself). |
| 42 | 4900 | JFrog Ltd (FROG) Shares Fall 10.8% -- What GF Score of 68 T… | **not relevant** | hard_negative | JFrog share price move (JFrog itself). |
| 43 | 2487 | JFrog Business Value Center: Celebrate the ROI of Your Soft… | **not relevant** | hard_negative | JFrog's own product announcement (Business Value Center). |
| 44 | 4713 | jfrog/jfrog-cli 2.127.0 | **not relevant** | hard_negative | JFrog CLI release notes (JFrog itself). |
| 45 | 2492 | JFrog Artifactory Supports LuaRocks Hosting for NGINX, Open… | **not relevant** | hard_negative | JFrog Artifactory adds LuaRocks (JFrog's own release). |
| 46 | 4966 | Technical Analysis of GitLab Inc. (CRYPTOCOM:GTLBUSD.P) - T… | **not relevant** | hard_negative | Headline only: a ticker/technical-analysis page for GitLab stock; no news. |
| 47 | 4124 | Amazon EC2 T8i instances are now available in South America… | **not relevant** | negative | New EC2 instance type in a region; generic cloud news. |
| 48 | 4126 | Amazon DynamoDB Streams now supports timestamp-based stream… | **not relevant** | negative | DynamoDB Streams feature; generic cloud database news. |
| 49 | 4195 | Amazon GameLift Servers adds CPU burstability for container… | **not relevant** | hard_negative | GameLift 'container fleets' CPU setting; containers, but not JFrog's market. |
| 50 | 4132 | Amazon MSK Connect and AWS Glue Schema Registry are now ava… | **not relevant** | hard_negative | Kafka 'Schema Registry' region launch; registry, but not artifacts or packages. |
| 51 | 4120 | AWS End User Messaging adds a guided wizard for RCS registr… | **not relevant** | hard_negative | AWS messaging 'wizard' for RCS registration; nothing to do with Wiz. |
| 52 | 4703 | Meta ships Muse Spark Open, its answer to China’s best open… | **not relevant** | negative | Meta open-weight model launch; generic AI news. |
| 53 | 5117 | EmbeddingGemma 2 Expands Beyond Text to Support Code, Image… | **not relevant** | negative | Google embedding model release; generic AI news. |
| 54 | 5119 | .NET 11 Release Candidate 2: Go-Live License and Updates Ac… | **not relevant** | negative | .NET 11 release candidate; generic developer platform news. |
| 55 | 5181 | Show HN: SimpleWM – i3 keybindings on top of native macOS S… | **not relevant** | negative | macOS window-manager helper (Show HN). |
| 56 | 5173 | Android app for running Anki Desktop, no proot | **not relevant** | negative | Anki desktop on Android (Show HN). |
| 57 | 5182 | Pcodec is 15% smaller and 2x faster than ClickHouse's Doubl… | **not relevant** | negative | Compression codec benchmark in ClickHouse. |
| 58 | 5108 | Exploited Fortinet Bug Linked to Stealthy FortiGate Implant… | **not relevant** | hard_negative | Fortinet bug exploited in a telecom hack; Palo Alto Networks only as the researcher. |
| 59 | 4102 | New Wiki Editor Experience | **not relevant** | hard_negative | Azure DevOps wiki editor update; a tracked product, but no supply-chain relevance. |
| 60 | 5032 | Show HN: Firmendaten: Parsed balance sheets for every Austr… | **not relevant** | hard_negative | Austrian company balance sheets; 'Firmendaten' contains 'mend'. |

## Disambiguation (`disambiguation.jsonl`, 28 rows)

Label: is the document about the named competitor? A word sense and a passing mention are both "no".

| id | doc | title | mention | about the competitor? | reason |
|---|---|---|---|---|---|
| 01 | 711 | Harness acquires Augment Code to advance the Autonomous SDLC | Harness | yes (harness) | Harness Inc. acquires Augment Code. |
| 02 | 5319 | Introducing Harness AI Budgets | Harness | yes (harness) | Harness AI Budgets, from Harness's own blog. |
| 03 | 4918 | harness/harness v3.0.0 \| Harness Open Source | Harness | yes (harness) | Harness Open Source 3.0 release (artifact registry included). |
| 04 | 85 | Introducing Chainguard Harness: Vulnerability hunting and r… | Harness | **no** (not harness) | Chainguard's product named Harness; the story is Chainguard's. |
| 05 | 4640 | Announcing: Agent Harness in Microsoft Agent Framework | Harness | **no** (not harness) | Microsoft Agent Framework's 'Agent Harness' runtime. |
| 06 | 5218 | Harness Engineering 101: The LLM is the brain, the harness… | Harness | **no** (not harness) | An AI agent harness (book on agent engineering). |
| 07 | 5423 | HAR 1.0: The open harness for AI coding workflows you can t… | Harness | **no** (not harness) | An open-source agent harness (HAR 1.0). |
| 08 | 1097 | Introducing new Snyk AppRisk integrations: Enhancing applic… | Harness | **no** (not harness) | Harness is one of several Snyk AppRisk integrations; the story is Snyk's. |
| 09 | 4771 | AI in Production: Panellists Highlight Silent Failures, Har… | Harness | **no** (not harness) | 'Harness engineering' for AI agents in an InfoQ panel. |
| 10 | 2521 | Introducing Socket Scanning for VS Code Marketplace Extensi… | Socket | yes (socket) | Socket launches VS Code extension scanning. |
| 11 | 3 | Hijacked TensorLake npm Turned Install Into Credential Risk | Socket | **no** (not socket) | Sonatype's post cites Socket's analysis once; the story is not Socket's. |
| 12 | 5197 | Prism – a fast, open-source IDM alternative for Windows and… | socket | **no** (not socket) | Network sockets in a download manager. |
| 13 | 2491 | ParaShells: Parallels Desktop Turns Appliance Install Into… | socket | **no** (not socket) | A Unix socket in a Parallels Desktop exploit (JFrog research). |
| 14 | 2498 | Docker Hub security advisory: personal access tokens transf… | Docker | yes (docker) | Docker Hub security advisory. |
| 15 | 2495 | From Containers to Sandboxes: Developer Trust in the Agenti… | Docker | yes (docker) | Docker on sandboxes for agents (Docker's own blog). |
| 16 | 5135 | Show HN: KiviDB, a faster Redis-compatible memory database… | Docker | **no** (not docker) | 'docker run' install line in an unrelated database project. |
| 17 | 22 | GitLab Transcend: Speed you can trust, all the way to produ… | Docker | **no** (not docker) | GitLab Transcend; Docker only as a package format GitLab supports. |
| 18 | 4694 | AI agents turn one request into about 20. Kubernetes is evo… | Docker | **no** (not docker) | Kubernetes roundup; a team 'containerized with Docker'. |
| 19 | 4432 | Mend.io upgrades 184 open source organizations to the Renov… | Mend | yes (mend) | Mend.io's Renovate plan upgrade. |
| 20 | 2499 | Trust Docker for the agents you don’t | Mend | **no** (not mend) | Docker's event recap lists Mend among partners; the story is Docker's. |
| 21 | 5334 | Wiz Sensor on GKE. 1-Click Runtime Protection, Now Native. | Wiz | yes (wiz) | Wiz runtime sensor on GKE. |
| 22 | 56 | How to use Cloudsmith as a dependency firewall | Wiz | **no** (not wiz) | Cloudsmith's dependency firewall post names Wiz among scanners. |
| 23 | 2669 | Trivy Compromised: Everything You Need to Know about the La… | Aqua | yes (aqua) | Aqua Security's Trivy was compromised (Wiz's write-up). |
| 24 | 4642 | The reality of using LLMs to identify open source risk: Hel… | Black Duck | yes (black-duck) | Black Duck on LLMs for SCA. |
| 25 | 113 | Chainguard Libraries now available on AWS Security Hub Exte… | Black Duck | **no** (not black-duck) | Chainguard Libraries post cites a Black Duck survey figure. |
| 26 | 5317 | GitHub package firewall in public preview | GitHub | yes (github) | GitHub's own package firewall. |
| 27 | 5166 | Show HN: Open-source AAS digital twin studio with live PLC… | GitHub | **no** (not github) | A Show HN project hosted on GitHub; no GitHub news. |
| 28 | 5156 | anchore/syft v1.55.0 | Syft | yes (anchore) | Release of Anchore's Syft SBOM tool. |

## Retrieval (`retrieval.jsonl`, 25 queries)

Relevant refs per query (`doc:<id>` is a corpus document, `kb:<id>` a JFrog KB page; titles in `corpus/`).

| id | query | style | filter | relevant | reason |
|---|---|---|---|---|---|
| 01 | TensorLake npm compromise | keyword | - | doc:3 Hijacked TensorLake npm Tur…; doc:42 Tensorlake SDK npm package…; doc:2519 TensorLake npm SDK Compromi… | The three write-ups of the TensorLake SDK attack. |
| 02 | Trivy supply chain attack | keyword | - | doc:2669 Trivy Compromised: Everythi…; doc:4224 What We Can Learn About Git… | Wiz's and Endor Labs' posts on the Trivy breach. |
| 03 | dependency firewall | keyword | - | doc:23 Dependency Firewall: Block…; doc:56 How to use Cloudsmith as a…; doc:59 A dependency firewall gives…; doc:5317 GitHub package firewall in… | GitLab's, Cloudsmith's and GitHub's firewall posts. |
| 04 | secret scanning | keyword | - | doc:2506 Secret scanning blocks Anth…; doc:2510 Purpose-built model for lea…; doc:5315 Updates to GitHub secret sc… | GitHub secret scanning updates. |
| 05 | Docker Hub personal access token vulnerability | keyword | - | doc:2498 Docker Hub security advisor… | Docker Hub PAT advisory. |
| 06 | Renovate | keyword | - | doc:4432 Mend.io upgrades 184 open s… | Mend Renovate plan post. |
| 07 | MCP server registry | keyword | - | doc:5128 Dynamic MCP Server Discover…; kb:10 /ai-catalog/mcp-registry/; doc:4157 AWS Agent Registry now supp… | Google Cloud MCP discovery, JFrog MCP Registry, AWS Agent Registry. |
| 08 | pinning GitHub Actions to commit SHAs | keyword | - | doc:92 Chainguard Actions is Gener… | Chainguard Actions post on SHA pinning. |
| 09 | VS Code extension scanning | keyword | - | doc:2521 Introducing Socket Scanning… | Socket's VS Code Marketplace scanning. |
| 10 | Nix binary cache | keyword | - | doc:44 Cloudsmith expands support… | Cloudsmith Nix support. |
| 11 | block malicious open source packages before they reach developers | semantic | - | doc:23 Dependency Firewall: Block…; doc:56 How to use Cloudsmith as a…; doc:59 A dependency firewall gives…; doc:5317 GitHub package firewall in…; doc:2475 Sonatype Unveils Nexus Repo…; doc:2 Trust What Ships: Your Repo…; kb:4 /curation/ | Firewall/curation at the point of request, without the word 'firewall'. |
| 12 | isolated microVM environments for running coding agents safely | semantic | - | doc:2497 Harbor + Docker Sandboxes:…; doc:2499 Trust Docker for the agents…; doc:81 Introducing Chainguard Work…; doc:2511 Local sandboxing for GitHub…; doc:2495 From Containers to Sandboxe… | Agent sandboxes from Docker, Chainguard and GitHub. |
| 13 | attackers hijacked a maintainer account to ship a backdoored command-line tool | semantic | - | doc:5367 OVHcloud CLI releases backd…; doc:4700 Hackers hijacked 1,900 GitH… | OVHcloud CLI backdoor; GhostAction hijacked accounts. |
| 14 | detecting leaked API keys with machine learning | semantic | - | doc:2510 Purpose-built model for lea… | GitHub's purpose-built secret detection model. |
| 15 | runtime threat detection for Kubernetes clusters | semantic | - | doc:5334 Wiz Sensor on GKE. 1-Click…; kb:7 /runtime/ | Wiz Sensor on GKE; JFrog Runtime. |
| 16 | governing which licenses our dependencies may use | semantic | - | doc:31 Open source license complia…; kb:2 /xray/ | GitHub license compliance; Xray license compliance. |
| 17 | treating AI models as part of the software supply chain | semantic | - | doc:4326 Extending Supply Chain Secu…; kb:9 /ai-catalog/; doc:2475 Sonatype Unveils Nexus Repo… | Anchore AI model scanning, JFrog AI Catalog, Nexus Pro+ screening models. |
| 18 | malware advisories across package ecosystems | semantic | - | doc:4743 Case Study: GitHub Expands… | GitHub/OpenSSF malware advisories in eight ecosystems. |
| 19 | using LLMs for software composition analysis | semantic | - | doc:4642 The reality of using LLMs t…; doc:5353 New OpenSSF Guide: Finding… | Black Duck on LLM-assisted SCA; OpenSSF AI vulnerability guide. |
| 20 | policy gates that block non-compliant releases | semantic | - | kb:6 /apptrust/ | JFrog AppTrust release policy gates. |
| 21 | credential-stealing worm spreading through npm packages | semantic | - | doc:51 The evolution of Shai-Hulud…; doc:3 Hijacked TensorLake npm Tur…; doc:42 Tensorlake SDK npm package…; doc:2519 TensorLake npm SDK Compromi… | Shai-Hulud lineage and the TensorLake compromise. |
| 22 | AI cost | filtered | harness | doc:5319 Introducing Harness AI Budg…; doc:5321 How We Cut Our AI Tooling B… | Harness posts on AI spend, filtered to Harness. |
| 23 | sandboxes for AI agents | filtered | docker | doc:2495 From Containers to Sandboxe…; doc:2497 Harbor + Docker Sandboxes:…; doc:2499 Trust Docker for the agents…; doc:2496 AI Agent Security Risks: Wh… | Docker's sandbox posts, filtered to Docker. |
| 24 | package firewall | filtered | github | doc:5317 GitHub package firewall in… | GitHub package firewall, filtered to GitHub. |
| 25 | compromised npm package | filtered | sonatype | doc:3 Hijacked TensorLake npm Tur… | Sonatype's TensorLake post, filtered to Sonatype. |

## The other datasets

- `guardrails.jsonl` (58 cases): the positives are real verified quotes from the evidence table and facts using the numbers their excerpt states; the negatives are planted by construction (each row's `reason` says what was changed).
- `fact_checker.jsonl` (5 groups, 30 claims): claims written against a real excerpt, 14 faithful and 16 with one planted error (`attack`: wrong_number, wrong_company, overstated_scope, derived_number, wrong_source, not_in_source, contradiction).
- `faithfulness.jsonl` (20 analyses): no labels; a separate judge model rates the stored analyses.
