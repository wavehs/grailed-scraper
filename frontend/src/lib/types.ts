export type SourceMode = 'live';
export type RunStatus =
  'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'interrupted' | 'cancelled';

export type ApiHealth = {
  status: 'ok';
  service: string;
  source_mode: SourceMode;
  request_id: string;
};

export type ParserHealth = {
  status: 'ready' | 'degraded' | 'unavailable';
  source_mode: SourceMode;
  transports: Record<string, boolean>;
  discovery: { available: boolean; status: string; discovered_at?: string; valid_until?: string };
  schema: {
    detected_at?: string;
    drift_score?: string;
    active_alerts: number;
    alerts: Array<{
      id: number;
      severity: string;
      message: string;
      details: Record<string, unknown>;
      created_at: string;
    }>;
  };
  proxies: ProxyStatus[];
  active_runs: number[];
  reasons: string[];
  versions: { scrapling?: string; camoufox?: string };
  circuits: Array<{ tier: string; host: string; proxy: string; state: string }>;
  compliance: {
    live_acknowledged: boolean;
    seller_identity_mode: 'none' | 'hashed' | 'plain';
    limits: { requests_per_minute: number; max_concurrency: number };
  };
  last_run?: {
    id: number;
    status: string;
    degraded: boolean;
    tier?: string;
    metrics: RunMetrics;
  };
};

export type ProxyStatus = {
  proxy: string;
  success_rate: number;
  successes: number;
  failures: number;
  consecutive_failures: number;
  cooling_down: boolean;
  cooldown_remaining_s?: number;
};

export type RunSummary = {
  id: number;
  mode: string;
  status: RunStatus;
  phase: string;
  dry_run: boolean;
  degraded: boolean;
  tier?: string;
  budget?: Record<string, number | boolean>;
  coverage?: string;
  requests_made: number;
  warnings: string[];
  created_at: string;
  started_at?: string;
  finished_at?: string;
  heartbeat_at?: string;
};

export type RunList = { data: RunSummary[]; total: number; limit: number; offset: number };
export type RunTask = {
  id: number;
  brand_id?: number;
  index_type: string;
  status: string;
  attempts: number;
  hits_collected: number;
  expected_hits?: number;
  coverage?: string;
  tier?: string;
  error?: string;
};
export type RunProgress = {
  status: RunStatus;
  phase: string;
  tier?: string;
  degraded: boolean;
  brands_total: number;
  brands_completed: number;
  tasks_total: number;
  tasks_done: number;
  hits_fetched: number;
  requests_made: number;
  coverage?: string;
  partial: boolean;
  truncated: boolean;
  current_brand?: string;
  tasks_failed: number;
  eta_seconds?: number;
  heartbeat_at?: string;
  warnings: string[];
  errors: Array<{
    task_id: number;
    brand_id?: number;
    index_type: string;
    code: string;
  }>;
};
export type RunReport = {
  run: RunSummary;
  stats: Record<string, unknown>;
  metrics: RunMetrics;
  coverage_by_brand: Record<string, string | null>;
  tasks: RunTask[];
};
export type RunMetrics = {
  requests_total: number;
  requests_by_tier: Record<string, number>;
  http_errors_by_code: Record<string, number>;
  retries: number;
  rate_limit_hits: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  cache_hit_rate: number;
  hits_fetched: number;
  listings_inserted: number;
  listings_updated: number;
  listings_invalid: number;
  duration_s: number;
};
export type FetchPlan = {
  mode: string;
  confirmation_token: string;
  budget: Record<string, number | boolean>;
  warnings: string[];
  tasks: Array<{
    brand_id: number;
    brand: string;
    index_type: string;
    index: string;
    status: string;
    strategy: string;
    max_hits: number | null;
  }>;
};
export type RunStartResponse =
  { dry_run: true; plan: FetchPlan } | { dry_run: false; run: RunSummary };

export type Mapping = {
  id: number;
  source_designer_name: string;
  source_slug?: string;
  listings_count: number;
  match_score: string;
  match_method: string;
  is_subbrand: boolean;
  state: 'verified' | 'review' | 'rejected';
};
export type Brand = {
  id: number;
  name: string;
  aliases: string[];
  include_subbrands: boolean;
  listings_count: number;
  status: 'verified' | 'review' | 'unresolved';
  mappings: Mapping[];
};
export type BrandList = { data: Brand[] };

export type DashboardProductType = 'footwear' | 'clothing' | 'accessories';

export type DashboardRow = {
  id: number;
  name: string;
  brand_name: string;
  category?: string;
  available_sizes: string[];
  available_conditions: string[];
  sold_count: number;
  exact_sold_count: number;
  active_count: number;
  median_sold_price: number | null;
  median_days_to_sell: string | null;
  median_sold_likes: string | null;
  liquidity_score: string | null;
  demand_score: string | null;
  price_score: string;
  confidence_score: string;
  market_opportunity_score: string | null;
  scoring_status: 'scored' | 'insufficient_sales' | 'insufficient_temporal_data';
  model_version: string;
  window_days: number;
  run_id: number;
};

export type ScoreComponent = {
  score: string;
  weight?: string;
  liquidity_weight?: string;
  demand_weight?: string;
};
export type ListingExample = {
  id: number;
  grailed_id: number;
  title: string;
  price: number;
  likes: number;
  sold_at?: string;
};
export type ModelGroupDetail = {
  id: number;
  name: string;
  brand: string;
  category?: string;
  group_type: string;
  model_version: string;
  window_days: number;
  run_id: number;
  input_digest: string;
  metrics: {
    sold_count: number;
    exact_sold_count: number;
    active_count: number;
    sell_through: string;
    median_sold_price?: number;
    median_days_to_sell?: string;
    median_sold_likes?: string;
    liquidity_score: string | null;
    demand_score: string | null;
    price_score: string;
    confidence_score: string;
    market_opportunity_score: string | null;
    scoring_status: 'scored' | 'insufficient_sales' | 'insufficient_temporal_data';
    components: Record<string, ScoreComponent>;
    confidence_factors: Record<string, unknown>;
    quality_summary: Record<string, unknown>;
    warnings: string[];
  };
  sold_examples: ListingExample[];
  active_examples: ListingExample[];
};

export type ModelRule = {
  id: number;
  group_id: number;
  brand_id: number;
  name: string;
  include_keywords: string[];
  exclude_keywords: string[];
  category?: string;
  is_active: boolean;
  matches_count: number;
};
export type RuleMatch = { id: number; title: string; status: string };

export type IdentityListing = {
  id: number;
  grailed_id: number;
  title: string;
  status: string;
  price: number;
  brand: string;
  category?: string;
  size?: string;
  color?: string;
  cover_photo_url?: string;
};
export type IdentityCandidate = {
  id: number;
  level: 'model' | 'physical';
  relation_type?: 'relist';
  status: 'pending' | 'auto_confirmed' | 'confirmed' | 'rejected';
  confidence: string;
  evidence: Record<string, unknown>;
  left: IdentityListing;
  right: IdentityListing;
};
export type IdentityCandidateList = {
  data: IdentityCandidate[];
  total: number;
  limit: number;
  offset: number;
};
export type IdentityHistory = {
  listing: IdentityListing;
  model_group?: { id: number; name: string; type: string; method: string; confidence: string };
  physical_item_id?: number;
  members: IdentityListing[];
  matches: Array<Record<string, unknown>>;
};

export type SettingOrigin = 'default' | 'env' | 'database';
export type SettingEntry = { value: string | number | boolean; origin: SettingOrigin };
export type SettingsResponse = { groups: Record<string, Record<string, SettingEntry>> };
export type DiscoveryResponse = {
  source: 'grailed';
  status: string;
  method?: string;
  discovered_at?: string;
  expires_at?: string;
  active_index?: string;
  sold_index?: string;
  brand_facet?: string;
  can_browse: boolean;
  pagination_limit?: number;
  max_hits_per_page?: number;
  schema_sample_size: number;
  schema_field_count: number;
  drift_score: number;
  alerts: Array<{ severity: string; kind: string; path: string }>;
};

export type CatalogListing = {
  id: number;
  grailed_id: number;
  title: string;
  brand: string;
  status: string;
  size?: string;
  color?: string;
  price: number;
  created_at?: string;
  sold_at?: string;
  last_seen_at: string;
  model_group_id?: number;
  model_name?: string;
  model_sold_count: number;
  model_active_count: number;
};
export type CatalogListingList = {
  data: CatalogListing[];
  total: number;
  limit: number;
  offset: number;
};
