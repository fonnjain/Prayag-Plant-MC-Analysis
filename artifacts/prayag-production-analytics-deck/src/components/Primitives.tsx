import type { ReactNode } from 'react';

export function SlideShell({
  children,
  dark = false,
}: {
  children: ReactNode;
  dark?: boolean;
}) {
  return (
    <div className={`w-screen h-screen overflow-hidden relative ${dark ? 'ink-slide' : 'paper-slide'}`}>
      {children}
    </div>
  );
}

export function Header({
  section,
  title,
  dark = false,
}: {
  section: string;
  title: string;
  dark?: boolean;
}) {
  return (
    <div className="slide-pad">
      <div className="kicker">{section}</div>
      <div className="rule mt-[2.4vh]" />
      <h1 className="headline mt-[2.6vh] max-w-[78vw]">{title}</h1>
    </div>
  );
}

export function Bullet({ n, children }: { n: string; children: ReactNode }) {
  return (
    <div className="bullet">
      <div className="bullet-mark">{n}</div>
      <div className="body-copy">{children}</div>
    </div>
  );
}

export function Footer({ page }: { page: string }) {
  return <div className="page-no">{page} / PRAYAG</div>;
}

export function Tag({ children, dark = false }: { children: ReactNode; dark?: boolean }) {
  return (
    <div className={`label inline-flex border px-[.8vw] py-[.55vh] ${dark ? 'border-[rgba(246,241,232,.25)]' : 'border-[rgba(20,33,43,.2)]'}`}>
      {children}
    </div>
  );
}