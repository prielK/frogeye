import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckIcon, CircleAlertIcon } from "lucide-react";
import { useId, useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { Wordmark } from "@/components/layout/Wordmark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type PersonaOut } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { initials, roleLabel, ROLES } from "@/lib/roles";
import { dayKey, formatDateline } from "@/lib/time";
import { cn } from "@/lib/utils";

const DEMO_PASSWORD = "frogeye-demo";

async function fetchPersonas(): Promise<PersonaOut[]> {
  const { data, response } = await api.GET("/api/v1/auth/personas");
  if (!data) throw new ApiError(response.status);
  return data;
}

function isRejectedCredentials(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 422);
}

function describeFailure(error: unknown): { title: string; hint: string } {
  if (isRejectedCredentials(error)) {
    return { title: "Invalid email or password.", hint: "Check both and try again, or choose a demo reader." };
  }
  if (error instanceof ApiError) {
    return { title: "FrogEye couldn't sign you in.", hint: "The server had a problem. Try again in a moment." };
  }
  return { title: "Can't reach FrogEye.", hint: "Check your connection and try again." };
}

function PersonaRoster({
  personas,
  selectedEmail,
  onChoose,
}: {
  personas: PersonaOut[];
  selectedEmail: string;
  onChoose: (persona: PersonaOut) => void;
}) {
  return (
    <ul aria-label="Demo readers" className="border-y">
      {personas.map((persona) => {
        const selected = persona.email === selectedEmail;
        return (
          <li key={persona.email} className="border-b last:border-b-0">
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onChoose(persona)}
              className={cn(
                "group flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors duration-150 ease-out-quart",
                "hover:bg-accent focus-visible:bg-accent focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                "aria-pressed:bg-accent",
              )}
            >
              <Avatar size="sm" aria-hidden>
                <AvatarFallback className="transition-colors duration-200 group-aria-pressed:bg-primary group-aria-pressed:text-primary-foreground group-aria-pressed:ring-0">
                  {initials(persona.name)}
                </AvatarFallback>
              </Avatar>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-2">
                  <span className="text-body font-medium text-foreground">{persona.name}</span>
                  <Badge variant="outline">{roleLabel(persona.role)}</Badge>
                </span>
                <span className="text-meta text-muted-foreground">{ROLES[persona.role]?.focus}</span>
              </span>
              <CheckIcon
                aria-hidden
                className="size-4 shrink-0 text-primary-ink opacity-0 transition-opacity duration-150 group-aria-pressed:opacity-100"
              />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function RosterSkeleton() {
  return (
    <ul aria-hidden className="border-y">
      {Array.from({ length: 5 }, (_, i) => (
        <li key={i} className="flex items-center gap-3 border-b px-5 py-2.5 last:border-b-0">
          <Skeleton className="size-6 rounded-full" />
          <span className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3.5 w-36" />
            <Skeleton className="h-3 w-52" />
          </span>
        </li>
      ))}
    </ul>
  );
}

function SignInPanel() {
  const { login } = useAuth();
  const titleId = useId();
  const emailId = useId();
  const passwordId = useId();
  const submitRef = useRef<HTMLButtonElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});

  const personas = useQuery({ queryKey: ["auth", "personas"], queryFn: fetchPersonas, staleTime: Infinity, retry: false });
  const signIn = useMutation({
    mutationFn: (credentials: { email: string; password: string }) => login(credentials.email, credentials.password),
  });

  const hasRoster = personas.isPending || (personas.data?.length ?? 0) > 0;
  const failure = signIn.isError ? describeFailure(signIn.error) : null;
  const rejected = isRejectedCredentials(signIn.error);

  function clearFeedback() {
    setFieldErrors({});
    if (signIn.isError) signIn.reset();
  }

  function choose(persona: PersonaOut) {
    setEmail(persona.email);
    setPassword(DEMO_PASSWORD);
    clearFeedback();
    submitRef.current?.focus();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const errors: { email?: string; password?: string } = {};
    if (!email.trim()) errors.email = "Enter your email address.";
    if (!password) errors.password = "Enter your password.";
    setFieldErrors(errors);
    if (errors.email || errors.password) return;
    signIn.mutate({ email: email.trim(), password });
  }

  return (
    <section aria-labelledby={titleId} className="w-full overflow-hidden rounded-lg border bg-card">
      <div className="px-5 pt-5 pb-4">
        <h2 id={titleId} className="text-body font-semibold">
          Sign in
        </h2>
        <p className="mt-1 text-body text-muted-foreground">
          {hasRoster
            ? "Pick a demo reader to fill in their details, or enter your own."
            : "Use your FrogEye email and password."}
        </p>
      </div>

      {personas.isPending ? (
        <RosterSkeleton />
      ) : personas.data && personas.data.length > 0 ? (
        <PersonaRoster personas={personas.data} selectedEmail={email} onChoose={choose} />
      ) : null}

      <form noValidate onSubmit={onSubmit} className={cn("flex flex-col gap-4 px-5 pt-5 pb-5", !hasRoster && "pt-1")}>
        <div className="flex flex-col gap-2">
          <Label htmlFor={emailId}>Email</Label>
          <Input
            id={emailId}
            name="email"
            type="email"
            inputMode="email"
            autoComplete="username"
            spellCheck={false}
            placeholder="name@jfrog.com"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              clearFeedback();
            }}
            aria-invalid={Boolean(fieldErrors.email) || rejected || undefined}
            aria-describedby={fieldErrors.email ? `${emailId}-error` : undefined}
          />
          {fieldErrors.email ? (
            <p id={`${emailId}-error`} className="text-meta text-threat">
              {fieldErrors.email}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor={passwordId}>Password</Label>
          <Input
            id={passwordId}
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              clearFeedback();
            }}
            aria-invalid={Boolean(fieldErrors.password) || rejected || undefined}
            aria-describedby={[fieldErrors.password && `${passwordId}-error`, `${passwordId}-hint`].filter(Boolean).join(" ")}
          />
          {fieldErrors.password ? (
            <p id={`${passwordId}-error`} className="text-meta text-threat">
              {fieldErrors.password}
            </p>
          ) : null}
          <p id={`${passwordId}-hint`} className="text-meta text-muted-foreground">
            Demo password: <span className="font-medium text-foreground">{DEMO_PASSWORD}</span>
          </p>
        </div>

        {failure ? (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-md bg-threat-soft px-3 py-2.5 text-body text-threat animate-in duration-200 ease-out-expo fade-in-0 slide-in-from-top-1"
          >
            <CircleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0" />
            <div className="flex flex-col gap-0.5">
              <p className="font-medium">{failure.title}</p>
              <p>{failure.hint}</p>
            </div>
          </div>
        ) : null}

        <Button ref={submitRef} type="submit" size="lg" loading={signIn.isPending} className="mt-1 w-full">
          {signIn.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </section>
  );
}

function PanelSkeleton() {
  return (
    <div role="status" aria-label="Checking your session" className="w-full rounded-lg border bg-card p-5">
      <Skeleton className="h-5 w-20" />
      <Skeleton className="mt-2 h-4 w-64" />
      <Skeleton className="mt-6 h-9 w-full" />
      <Skeleton className="mt-4 h-9 w-full" />
      <Skeleton className="mt-6 h-10 w-full" />
    </div>
  );
}

export function LoginPage() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const [today] = useState(() => new Date());

  if (user) {
    const from = (location.state as { from?: { pathname?: string; search?: string } } | null)?.from;
    const back = from?.pathname && from.pathname !== "/login" ? `${from.pathname}${from.search ?? ""}` : "/";
    return <Navigate to={back} replace />;
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <title>Sign in · FrogEye</title>
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 pt-6 pb-4 sm:px-6 lg:px-10">
        <Wordmark size="lg" />
        <div className="flex items-center gap-3">
          <time dateTime={dayKey(today.toISOString())} className="hidden text-meta text-muted-foreground sm:block">
            {formatDateline(today.getTime())}
          </time>
          <ThemeToggle />
        </div>
      </header>
      <div aria-hidden className="mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-10">
        <div className="h-px bg-foreground" />
      </div>

      {/* Top-aligned, not centered: the panel grows downward on errors without moving the page. */}
      <main className="mx-auto grid w-full max-w-6xl flex-1 content-start gap-10 px-4 py-10 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,25rem)] lg:items-start lg:gap-20 lg:px-10 lg:pt-16 lg:pb-20">
        <section aria-label="About FrogEye" className="max-w-xl">
          <h1 className="font-headline text-display">
            What changed in the market today, and what to do about it.
          </h1>
          {/* The standfirst under a display headline is body in ink, as the Briefing's lead is. */}
          <p className="mt-4 max-w-[46ch] text-body text-foreground">
            FrogEye collects public market signals every day and turns them into a briefing for each JFrog team, with
            every claim linked to its source.
          </p>
        </section>
        {loading ? <PanelSkeleton /> : <SignInPanel />}
      </main>
    </div>
  );
}
