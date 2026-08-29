import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide15() {
  return (
    <SlideShell>
      <Header section="15 / OPERATIONS" title="Operations keeps the supporting context close" />
      <div className="grid grid-cols-3 gap-[1.2vw] px-[7vw] pt-[5vh]">
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">01</div><div className="body-copy mt-[2vh]">Materials and maintenance</div></div>
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">02</div><div className="body-copy mt-[2vh]">Manpower and labour</div></div>
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">03</div><div className="body-copy mt-[2vh]">Yield and mixer</div></div>
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">04</div><div className="body-copy mt-[2vh]">Toolroom and wastage</div></div>
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">05</div><div className="body-copy mt-[2vh]">Compound</div></div>
        <div className="panel h-[20vh]"><div className="stat-number text-[4vw]">06</div><div className="body-copy mt-[2vh]">Each area remains available from the Operations navigation</div></div>
      </div>
      <Footer page="15" />
    </SlideShell>
  );
}