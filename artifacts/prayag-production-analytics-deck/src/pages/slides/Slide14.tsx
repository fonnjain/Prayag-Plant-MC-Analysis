import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide14() {
  return (
    <SlideShell dark>
      <Header section="14 / INTERPRETATION" title="The right conclusion is nuanced" dark />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[4vh]">
        <div className="panel-dark h-[21vh]"><div className="stat-number">81.23%</div><div className="body-copy mt-[1.8vh]">of August demand had direct modelling coverage</div></div>
        <div className="panel-dark h-[21vh]"><div className="label">Coverage</div><div className="body-copy mt-[2vh]">Fallbacks extend planning coverage instead of forcing blind exclusions</div></div>
        <div className="panel-dark h-[21vh]"><div className="label">Pipe</div><div className="body-copy mt-[2vh]">Fallback estimates look directionally usable but need review at the edges</div></div>
        <div className="panel-dark h-[21vh]"><div className="label">Fittings</div><div className="body-copy mt-[2vh]">Averages contain substantial optimism and outlier risk</div></div>
      </div>
      <div className="absolute bottom-[10vh] left-[7vw] right-[7vw]">
        <div className="signal-line" />
        <div className="headline mt-[2vh] text-[2.6vw]">“Schedulable” does not mean “equally well modelled”</div>
      </div>
      <Footer page="14" />
    </SlideShell>
  );
}