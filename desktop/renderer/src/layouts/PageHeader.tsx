import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  description: string;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
};

export function PageHeader({ title, description, actions, children, className = "" }: PageHeaderProps) {
  return (
    <section className={`hero section-heading ${className}`.trimEnd()}>
      <div className="section-heading-row">
        <div className="section-heading-copy">
          <h1>{title}</h1>
          <p className="hero-copy">{description}</p>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
