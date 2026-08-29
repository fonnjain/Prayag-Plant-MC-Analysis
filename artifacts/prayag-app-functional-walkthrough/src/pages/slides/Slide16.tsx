import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide16() {
  return (
    <SlideShell dark>
      <Header section="16 / DATA & AUDIT" title="Data & Audit protects decision quality" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel-dark h-[17vh]"><div className="label">Sources</div><div className="body-copy mt-[1.7vh]">Sources shows detected source workbooks</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">Manifest</div><div className="body-copy mt-[1.7vh]">Manifest shows ingestion coverage and parse notes</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">Data</div><div className="body-copy mt-[1.7vh]">Data shows health and freshness signals</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">Confirmation</div><div className="body-copy mt-[1.7vh]">Confirmation manages issue tiers, fingerprints, acknowledgements, and sign-off</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">Verification</div><div className="body-copy mt-[1.7vh]">Verification provides read-only reconciliation and CSV export</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">Refresh</div><div className="body-copy mt-[1.7vh]">Refresh reloads source data while preserving the selected period</div></div>
      </div>
      <Footer page="16" />
    </SlideShell>
  );
}