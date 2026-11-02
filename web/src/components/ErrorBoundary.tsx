import { Component, type ErrorInfo, type ReactNode } from "react";

/** One bad field in a run should not blank the page: show a way back instead. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="studio">
        <main className="studio-error">
          <h1 className="serif">Something went wrong on this page.</h1>
          <p className="muted">Reload to try again, or go back to the rooms.</p>
          <div className="studio-error-actions">
            <button className="btn primary" onClick={() => window.location.reload()}>
              Reload
            </button>
            <a className="btn" href="/">
              Back to the rooms
            </a>
          </div>
        </main>
      </div>
    );
  }
}
