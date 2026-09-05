/* GitHub connect panel — talks to GitHub's API directly from the browser
   with the user's own personal access token. The token is kept in
   localStorage only; it is never sent to the Aali server. */

const TOKEN_KEY = "aali_gh_token";

export function getGithubToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setGithubToken(token: string) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export interface GithubRepo {
  id: number;
  full_name: string;
  html_url: string;
  description: string | null;
  stargazers_count: number;
  language: string | null;
  updated_at: string;
  private: boolean;
}

export interface GithubUser {
  login: string;
  avatar_url: string;
  html_url: string;
}

async function ghFetch(path: string, token: string) {
  const res = await fetch(`https://api.github.com${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
    },
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("رمز GitHub غير صالح أو منتهي");
    throw new Error(`GitHub API: HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchGithubUser(token: string): Promise<GithubUser> {
  return ghFetch("/user", token);
}

export async function fetchGithubRepos(token: string): Promise<GithubRepo[]> {
  const repos: GithubRepo[] = await ghFetch(
    "/user/repos?sort=updated&per_page=30&affiliation=owner,collaborator",
    token
  );
  return repos;
}
