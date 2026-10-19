"use client";

/**
 * Error boundary for one chart or panel: a render error in a single chart
 * shows an inline "could not be displayed" state with a retry instead of
 * taking the whole page into the route error boundary. `resetKey` clears
 * the error when the chart's input changes (new filters, new data).
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

export interface ChartBoundaryProps {
  children: ReactNode;
  /** Rendered instead of `children` after an error; `reset` retries. */
  fallback: (reset: () => void) => ReactNode;
  /** A change of this value clears a caught error. */
  resetKey?: unknown;
}

interface State {
  failed: boolean;
  key: unknown;
}

export class ChartBoundary extends Component<ChartBoundaryProps, State> {
  state: State = { failed: false, key: this.props.resetKey };

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: ChartBoundaryProps, state: State): Partial<State> | null {
    return Object.is(props.resetKey, state.key) ? null : { failed: false, key: props.resetKey };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Chart failed to render", error, info.componentStack);
  }

  private reset = () => this.setState({ failed: false });

  render(): ReactNode {
    return this.state.failed ? this.props.fallback(this.reset) : this.props.children;
  }
}
