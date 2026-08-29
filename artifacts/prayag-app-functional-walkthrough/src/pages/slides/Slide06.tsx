import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide06() {
  return (
    <SlideShell dark>
      <Header section="06 / EXPLANATION" title="The dashboard explains the number" />
      <div className="grid grid-cols-[.9fr_1.1fr] gap-[6vw] px-[7vw] pt-[4vh]">
        <div className="panel-dark flex h-[36vh] flex-col justify-between">
          <div className="label">Metric logic</div>
          <div className="headline text-[3.25vw]">Measure.<br />Compare.<br />Explain.</div>
          <div className="signal-line" />
        </div>
        <div className="flex flex-col gap-[2.6vh] pt-[1vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">OEE separates availability, performance, and quality</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Utilisation shows how much tracked capacity was used</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Output efficiency compares production with ideal-output baselines</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Good output, rejection, actual hours, and ideal hours stay visible</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">The glossary makes metric definitions available in context</div></div>
        </div>
      </div>
      <Footer page="06" />
    </SlideShell>
  );
}