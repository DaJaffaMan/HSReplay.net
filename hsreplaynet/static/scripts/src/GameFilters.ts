import { GameReplay, GlobalGamePlayer } from "./interfaces";
import { BnetGameType, FormatType } from "./hearthstone";
import { getHeroCard } from "./helpers";
import CardData from "./CardData";
import { isSameMonth, subMonths } from "date-fns";

export function nameMatch(game: GameReplay, name: string): boolean {
	const players = [game.friendly_player, game.opposing_player];
	return players.some(player => {
		const pName = player.name.toLowerCase();
		if (pName.indexOf(name) !== -1) {
			return true;
		}
		if (name === '"' + pName + '"') {
			return true;
		}
		return false;
	});
}

export function modeMatch(game: GameReplay, mode: string): boolean {
	switch (mode) {
		case "arena":
			return game.global_game.game_type === BnetGameType.BGT_ARENA;
		case "ranked":
			return (
				game.global_game.game_type ===
					BnetGameType.BGT_RANKED_STANDARD ||
				game.global_game.game_type === BnetGameType.BGT_RANKED_WILD
			);
		case "casual":
			return (
				game.global_game.game_type ===
					BnetGameType.BGT_CASUAL_STANDARD ||
				game.global_game.game_type === BnetGameType.BGT_CASUAL_WILD
			);
		case "brawl":
			return (
				game.global_game.game_type ===
					BnetGameType.BGT_TAVERNBRAWL_1P_VERSUS_AI ||
				game.global_game.game_type ===
					BnetGameType.BGT_TAVERNBRAWL_2P_COOP ||
				game.global_game.game_type === BnetGameType.BGT_TAVERNBRAWL_PVP
			);
		case "friendly":
			return game.global_game.game_type === BnetGameType.BGT_FRIENDS;
		case "adventure":
			return game.global_game.game_type === BnetGameType.BGT_VS_AI;
		default:
			return true;
	}
}

export function formatMatch(
	game: GameReplay,
	format: string,
	mode: string,
): boolean {
	if (!(!mode || mode === "ranked" || mode === "casual")) {
		return true;
	}
	switch (format) {
		case "standard":
			return game.global_game.format === FormatType.FT_STANDARD;
		case "wild":
			return game.global_game.format === FormatType.FT_WILD;
		default:
			return true;
	}
}

export function resultMatch(game: GameReplay, result: string): boolean {
	switch (result) {
		case "won":
			return game.won;
		case "lost":
			return !game.won;
		default:
			return true;
	}
}

export function seasonMatch(game: GameReplay, season: string): boolean {
	const currentSeasonDate = Date.now();
	switch (season) {
		case "current":
			return isSameMonth(game.global_game.match_start, currentSeasonDate);
		case "previous":
			return isSameMonth(
				game.global_game.match_start,
				subMonths(currentSeasonDate, 1),
			);
		default:
			return true;
	}

	return false;
}

export function heroMatch(
	cardData: CardData,
	player: GlobalGamePlayer,
	hero: string,
): boolean {
	if (!cardData) {
		return false;
	}
	const card = getHeroCard(cardData, player);
	if (!card) {
		return false;
	}
	return card.cardClass.toLowerCase() === hero.toLowerCase();
}
