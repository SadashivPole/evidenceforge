const implementationBoundaries = [
  "FastAPI API boundary",
  "Next.js web boundary",
  "PostgreSQL readiness check",
  "Versioned migrations",
  "Docker Compose local environment",
];

export default function Home() {
  return (
    <main className="shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Phase 1A · Application skeleton</p>
        <h1 id="page-title">EvidenceForge</h1>
        <p className="lede">
          A local-first workbench for evidence-bound security questionnaire
          responses.
        </p>
        <p className="status">
          The application boundary is ready for the next implementation slice.
        </p>
      </section>

      <section className="panel" aria-labelledby="boundary-title">
        <h2 id="boundary-title">Implemented boundary</h2>
        <ul>
          {implementationBoundaries.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="note">
          Evidence ingestion, retrieval, generation, authentication, and
          authorization are intentionally not implemented in Phase 1A.
        </p>
      </section>
    </main>
  );
}
