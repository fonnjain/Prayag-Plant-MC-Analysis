import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide08() {
  return (
    <SlideShell dark>
      <Header section="08 / MANAGEMENT REPORTS" title="Management reports make recurring review repeatable" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel-dark h-[17vh]"><div className="label">01</div><div className="body-copy mt-[1.7vh]">Segment labour</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">02</div><div className="body-copy mt-[1.7vh]">Pipe, Garden Pipe, HDPE, and GOM summaries</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">03</div><div className="body-copy mt-[1.7vh]">Moulding and Tank KH, VN, and WB summaries</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">04</div><div className="body-copy mt-[1.7vh]">PTMT mould efficiency and pipe moulds summaries</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">05</div><div className="body-copy mt-[1.7vh]">PTMT moulds and compound compilation</div></div>
        <div className="panel-dark h-[17vh]"><div className="label">06</div><div className="body-copy mt-[1.7vh]">Individual XLSX downloads or one ZIP bundle</div></div>
      </div>
      <Footer page="08" />
    </SlideShell>
  );
}