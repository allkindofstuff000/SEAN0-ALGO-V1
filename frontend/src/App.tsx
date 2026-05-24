import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";

// Pages
import LiveBot from "./pages/LiveBot";
import RsiEma from "./pages/RsiEma";
import XauScalp from "./pages/XauScalp";
import Crypto from "./pages/Crypto";
import IctEth from "./pages/IctEth";
import IctXau from "./pages/IctXau";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 60 * 1000,
    },
  },
});

function Router() {
  return (
    <Switch>
      <Route path="/" component={LiveBot} />
      <Route path="/live-bot" component={LiveBot} />
      <Route path="/rsi-ema" component={RsiEma} />
      <Route path="/xau-scalp" component={XauScalp} />
      <Route path="/crypto" component={Crypto} />
      <Route path="/ict-eth" component={IctEth} />
      <Route path="/ict-xau" component={IctXau} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
