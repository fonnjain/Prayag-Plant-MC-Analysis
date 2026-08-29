import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide07() {
  return (
    <SlideShell>
      <Header section="07 / REPORTS" title="Reports turn the operating view into analysis" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel h-[17vh]"><div className="label">Production</div><div className="body-copy mt-[1.7vh]">Pipe, Garden Pipe, and HDPE M/C summaries</div></div>
        <div className="panel h-[17vh]"><div className="label">Injection</div><div className="body-copy mt-[1.7vh]">PTMT Injection and CP Injection M/C summaries</div></div>
        <div className="panel h-[17vh]"><div className="label">Moulds</div><div className="body-copy mt-[1.7vh]">Mould-wise Summary and Mould Age-in-Efficiency</div></div>
        <div className="panel h-[17vh]"><div className="label">Tank</div><div className="body-copy mt-[1.7vh]">Tank Litre Summary</div></div>
        <div className="panel h-[17vh]"><div className="label">Drivers</div><div className="body-copy mt-[1.7vh]">Compound, segment-cost, and utilisation reports</div></div>
        <div className="panel h-[17vh]"><div className="label">Evidence</div><div className="body-copy mt-[1.7vh]">Charts, tables, reconciliation, and optional AI narrative</div></div>
      </div>
      <Footer page="07" />
    </SlideShell>
  );
}