import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide06() {
  return (
    <SlideShell dark>
      <Header section="06 / TRUST LAYER" title="Trust is visible in the product" dark />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel-dark"><div className="label">Confirmation status</div><div className="body-copy mt-[2vh]">First-class: ok, warning, or error</div></div>
        <div className="panel-dark"><div className="label">Gated figures</div><div className="body-copy mt-[2vh]">Error-gated figures are marked provisional instead of looking clean</div></div>
        <div className="panel-dark"><div className="label">Manager sign-off</div><div className="body-copy mt-[2vh]">Can release an error gate with an audit trail</div></div>
        <div className="panel-dark"><div className="label">Fingerprint</div><div className="body-copy mt-[2vh]">Sign-off is bound to a source-data fingerprint</div></div>
      </div>
      <div className="absolute bottom-[11vh] left-[7vw] right-[7vw] panel-dark">
        <div className="label">Verification boundary</div>
        <div className="body-copy mt-[1.4vh]">Read-only verification explains mismatches without correcting facts</div>
      </div>
      <Footer page="06" />
    </SlideShell>
  );
}