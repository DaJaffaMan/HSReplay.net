import { cookie } from "cookie_js";
import {
	Launcher,
	launcher as launchJoust,
	release as joustRelease,
} from "joust";
import * as Sentry from "@sentry/browser";
import React from "react";
import { cardArt, joustAsset, joustStaticFile } from "./helpers";
import BatchingMiddleware from "./metrics/BatchingMiddleware";
import InfluxMetricsBackend from "./metrics/InfluxMetricsBackend";
import MetricsReporter from "./metrics/MetricsReporter";
import UserData from "./UserData";
import i18next from "i18next";

export default class JoustEmbedder {
	public turn: number = null;
	public reveal: boolean = null;
	public swap: boolean = null;
	public launcher: Launcher = null;
	public onTurn: (turn: number) => void = null;
	public onToggleSwap: (swap: boolean) => void = null;
	public onToggleReveal: (reveal: boolean) => void = null;
	private url: string = null;

	public embed(target: HTMLElement, t: i18next.TFunction) {
		this.prepare(target, t);
		this.render();
	}

	public prepare(target: HTMLElement, t: i18next.TFunction) {
		// find container
		if (!target) {
			throw new Error("No target specified");
		}

		if (!launchJoust) {
			console.error("Could not load Joust");
			const joustUrl = joustStaticFile("joust.js");
			target.innerHTML =
				'<p class="alert alert-danger">' +
				`<strong>${t("Loading failed:")}</strong> ` +
				`<p>${t(
					"Replay applet (Joust) could not be loaded. Please ensure you can access {joustUrl}.",
					{
						joustUrl: `<a href="${joustUrl}">${joustUrl}</a>`,
					},
				)}</p>` +
				`<p>${t(
					"Otherwise try clearing your cache and refreshing this page.",
				)}</p>`;
			// could also offer document.location.reload(true)
			return;
		}

		const launcher: Launcher = launchJoust(target);
		this.launcher = launcher;
		const release = joustRelease();

		UserData.create();

		// setup RavenJS/Sentry
		let logger = null;
		const dsn = JOUST_RAVEN_DSN_PUBLIC;
		if (dsn) {
			Sentry.init({
				dsn,
				release,
				environment: JOUST_RAVEN_ENVIRONMENT || "development",
			});
			Sentry.configureScope(scope => {
				scope.setTag("react", React.version);
				const username = UserData.getUsername();
				if (username) {
					scope.setUser({ username });
				}
			});
			logger = (err: string | Error) => {
				if (typeof err === "string") {
					Sentry.captureMessage(err);
				} else {
					Sentry.captureException(err);
				}
				const message = err["message"] ? err["message"] : err;
				console.error(message);
			};
			launcher.logger(logger);
		}

		// setup graphics
		launcher.assets((asset: string) => joustAsset(asset));
		launcher.cardArt((cardId: string) => cardArt(cardId));

		// setup language
		const locale = UserData.getHearthstoneLocale();
		launcher.locale(locale);

		// setup influx
		const endpoint = INFLUX_DATABASE_JOUST;
		if (endpoint) {
			// track startup time
			let realtimeElapsed = 0;
			let startupTime = null;
			let measuring = true;
			if ("visibilityState" in document) {
				measuring = document.visibilityState === "visible";
				document.addEventListener("visibilitychange", () => {
					if (measuring && startupTime) {
						realtimeElapsed += Date.now() - startupTime;
					}
					measuring = document.visibilityState === "visible";
					startupTime = Date.now();
				});
			}
			let metrics = null;
			const track = (series, values, tags) => {
				if (!tags) {
					tags = {};
				}
				tags["release"] = release;
				tags["locale"] = locale;
				if (series === "startup") {
					startupTime = Date.now();
				}
				metrics.writePoint(series, values, tags);
			};
			metrics = new MetricsReporter(
				new BatchingMiddleware(
					new InfluxMetricsBackend(endpoint),
					(): void => {
						const values = {
							percentage: launcher.percentageWatched,
							seconds: launcher.secondsWatched,
							duration: launcher.replayDuration,
							realtime: undefined,
						};
						if (measuring && startupTime) {
							realtimeElapsed += Date.now() - startupTime;
							values.realtime = realtimeElapsed / 1000;
						}
						metrics.writePoint("watched", values, {
							realtime_fixed: 1,
						});
					},
				),
				(series: string): string => "joust_" + series,
			);
			launcher.events(track);
		}

		// turn linking
		if (this.turn !== null) {
			launcher.startAtTurn(this.turn);
		}
		launcher.onTurn((newTurn: number) => {
			this.turn = newTurn;
			this.onTurn && this.onTurn(newTurn);
		});

		if (this.reveal !== null) {
			launcher.startRevealed(this.reveal);
		}
		launcher.onToggleReveal((newReveal: boolean) => {
			this.reveal = newReveal;
			this.onToggleReveal && this.onToggleReveal(newReveal);
		});

		if (this.swap !== null) {
			launcher.startSwapped(this.swap);
		}
		launcher.onToggleSwap((newSwap: boolean) => {
			this.swap = newSwap;
			this.onToggleSwap && this.onToggleSwap(newSwap);
		});

		// autoplay
		launcher.startPaused(cookie.get("disable-autoplay", false));

		// setup player names
		if (typeof launcher.stripBattletags === "function") {
			launcher.stripBattletags(true);
		}
		if (typeof launcher.addPlayerName === "function") {
			for (let i = 1; true; i++) {
				const key = "data-player" + i;
				if (!target.hasAttribute(key)) {
					break;
				}
				const playerName = target.getAttribute(key);
				launcher.addPlayerName(playerName);
			}
		}

		// initialize joust
		let url = target.getAttribute("data-replayurl");
		if (!url.match(/^http(s?):\/\//) && !url.startsWith("/")) {
			url = "/" + url;
		}
		this.url = url;
	}

	public render(cb?: () => any) {
		if (!this.url) {
			throw new Error("Not prepared"); // you are
		}
		this.launcher.fromUrl(this.url, cb);
	}
}
