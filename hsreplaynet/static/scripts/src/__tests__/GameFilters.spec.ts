import {
	formatMatch,
	heroMatch,
	modeMatch,
	nameMatch,
	resultMatch,
	seasonMatch,
} from "../GameFilters";
import { GameReplay, GlobalGame, GlobalGamePlayer } from "../interfaces";
import * as TypeMoq from "typemoq";
import { BnetGameType, FormatType } from "../hearthstone";
import CardData from "../CardData";
import { CardData as HearthstoneJSONCardData } from "hearthstonejson-client";
import { subMonths } from "date-fns";

describe("nameMatch", () => {
	const createReplay = (
		player1Name: string,
		player2Name?: string,
	): GameReplay => {
		const mockedGameReplay: TypeMoq.IMock<GameReplay> = TypeMoq.Mock.ofType<
			GameReplay
		>();
		const mockedPlayer1: TypeMoq.IMock<
			GlobalGamePlayer
		> = TypeMoq.Mock.ofType<GlobalGamePlayer>();

		mockedPlayer1.setup(x => x.name).returns(() => player1Name);
		mockedGameReplay
			.setup(x => x.friendly_player)
			.returns(() => mockedPlayer1.object);

		if (player2Name) {
			const mockedPlayer2: TypeMoq.IMock<
				GlobalGamePlayer
			> = TypeMoq.Mock.ofType<GlobalGamePlayer>();

			mockedPlayer2.setup(x => x.name).returns(() => player2Name);
			mockedGameReplay
				.setup(x => x.opposing_player)
				.returns(() => mockedPlayer2.object);
		}

		return mockedGameReplay.object;
	};

	test("matches exact player names", () => {
		const replay: GameReplay = createReplay(
			"Test player foo",
			"Test player bar",
		);
		expect(nameMatch(replay, "test player foo")).toBe(true);
		expect(nameMatch(replay, "test player bar")).toBe(true);
		expect(nameMatch(replay, "test player baz")).toBe(false);
	});

	test("matches partial player names", () => {
		const replay: GameReplay = createReplay(
			"Test player foo",
			"Test player bar",
		);
		expect(nameMatch(replay, "foo")).toBe(true);
		expect(nameMatch(replay, "bar")).toBe(true);
		expect(nameMatch(replay, "baz")).toBe(false);
	});
});

describe("modeMatch", () => {
	const createReplay = (gameType: BnetGameType): GameReplay => {
		const mockedGameReplay: TypeMoq.IMock<GameReplay> = TypeMoq.Mock.ofType<
			GameReplay
		>();
		const mockedGlobalGame: TypeMoq.IMock<GlobalGame> = TypeMoq.Mock.ofType<
			GlobalGame
		>();

		mockedGlobalGame.setup(x => x.game_type).returns(() => gameType);
		mockedGameReplay
			.setup(x => x.global_game)
			.returns(() => mockedGlobalGame.object);

		return mockedGameReplay.object;
	};

	const arenaReplay: GameReplay = createReplay(BnetGameType.BGT_ARENA);
	const rankedStandardReplay: GameReplay = createReplay(
		BnetGameType.BGT_RANKED_STANDARD,
	);
	const rankedWildReplay: GameReplay = createReplay(
		BnetGameType.BGT_RANKED_WILD,
	);
	const casualStandardReplay: GameReplay = createReplay(
		BnetGameType.BGT_CASUAL_STANDARD,
	);
	const casualWildReplay: GameReplay = createReplay(
		BnetGameType.BGT_CASUAL_WILD,
	);
	const tavernAiReplay: GameReplay = createReplay(
		BnetGameType.BGT_TAVERNBRAWL_1P_VERSUS_AI,
	);
	const tavernCoopReplay: GameReplay = createReplay(
		BnetGameType.BGT_TAVERNBRAWL_2P_COOP,
	);
	const tavernPvpReplay: GameReplay = createReplay(
		BnetGameType.BGT_TAVERNBRAWL_PVP,
	);
	const friendsReplay: GameReplay = createReplay(BnetGameType.BGT_FRIENDS);
	const adventureReplay: GameReplay = createReplay(BnetGameType.BGT_VS_AI);

	test("returns true for matching game types", () => {
		expect(modeMatch(arenaReplay, "arena")).toBe(true);
		expect(modeMatch(rankedStandardReplay, "ranked")).toBe(true);
		expect(modeMatch(rankedWildReplay, "ranked")).toBe(true);
		expect(modeMatch(casualStandardReplay, "casual")).toBe(true);
		expect(modeMatch(casualWildReplay, "casual")).toBe(true);
		expect(modeMatch(tavernAiReplay, "brawl")).toBe(true);
		expect(modeMatch(tavernCoopReplay, "brawl")).toBe(true);
		expect(modeMatch(tavernPvpReplay, "brawl")).toBe(true);
		expect(modeMatch(friendsReplay, "friendly")).toBe(true);
		expect(modeMatch(adventureReplay, "adventure")).toBe(true);
	});

	test("returns false for non-matching game types", () => {
		expect(modeMatch(adventureReplay, "arena")).toBe(false);
		expect(modeMatch(friendsReplay, "ranked")).toBe(false);
		expect(modeMatch(tavernAiReplay, "casual")).toBe(false);
		expect(modeMatch(casualStandardReplay, "brawl")).toBe(false);
		expect(modeMatch(arenaReplay, "friendly")).toBe(false);
		expect(modeMatch(rankedStandardReplay, "adventure")).toBe(false);
	});

	test("returns true for unrecognized modes", () => {
		const replay: GameReplay = createReplay(BnetGameType.BGT_UNKNOWN);
		expect(modeMatch(replay, "not_a_recognized_mode")).toBe(true);
	});
});

describe("formatMatch", () => {
	const createReplay = (format: FormatType): GameReplay => {
		const mockedGameReplay: TypeMoq.IMock<GameReplay> = TypeMoq.Mock.ofType<
			GameReplay
		>();
		const mockedGlobalGame: TypeMoq.IMock<GlobalGame> = TypeMoq.Mock.ofType<
			GlobalGame
		>();

		mockedGlobalGame.setup(x => x.format).returns(() => format);
		mockedGameReplay
			.setup(x => x.global_game)
			.returns(() => mockedGlobalGame.object);

		return mockedGameReplay.object;
	};

	const standardReplay: GameReplay = createReplay(FormatType.FT_STANDARD);
	const wildReplay: GameReplay = createReplay(FormatType.FT_WILD);

	test("returns true for matching formats", () => {
		expect(formatMatch(standardReplay, "standard", "ranked")).toBe(true);
		expect(formatMatch(standardReplay, "standard", "casual")).toBe(true);
		expect(formatMatch(wildReplay, "wild", "ranked")).toBe(true);
		expect(formatMatch(wildReplay, "wild", "casual")).toBe(true);
	});

	test("returns false for non-matching formats", () => {
		expect(formatMatch(wildReplay, "standard", "ranked")).toBe(false);
		expect(formatMatch(wildReplay, "standard", "casual")).toBe(false);
		expect(formatMatch(standardReplay, "wild", "ranked")).toBe(false);
		expect(formatMatch(standardReplay, "wild", "casual")).toBe(false);
	});

	test("returns true for unrecognized formats", () => {
		expect(
			formatMatch(standardReplay, "not_a_real_format", "not_a_real_mode"),
		).toBe(true);
		expect(
			formatMatch(wildReplay, "not_a_real_format", "not_a_real_mode"),
		).toBe(true);
	});
});

describe("resultMatch", () => {
	const createReplay = (won: boolean): GameReplay => {
		const mockedGameReplay: TypeMoq.IMock<GameReplay> = TypeMoq.Mock.ofType<
			GameReplay
		>();
		mockedGameReplay.setup(x => x.won).returns(() => won);
		return mockedGameReplay.object;
	};

	const winningGame: GameReplay = createReplay(true);
	const losingGame: GameReplay = createReplay(false);

	test("correctly matches the game result", () => {
		expect(resultMatch(winningGame, "won")).toBe(true);
		expect(resultMatch(winningGame, "lost")).toBe(false);
		expect(resultMatch(losingGame, "won")).toBe(false);
		expect(resultMatch(losingGame, "lost")).toBe(true);
	});

	test("returns true for unrecognized results", () => {
		expect(resultMatch(winningGame, "called_it_even")).toBe(true);
		expect(resultMatch(losingGame, "its_a_toss_up")).toBe(true);
	});
});

describe("seasonMatch", () => {
	const createReplay = (seasonDate: Date): GameReplay => {
		const mockedGameReplay: TypeMoq.IMock<GameReplay> = TypeMoq.Mock.ofType<
			GameReplay
		>();
		const mockedGlobalGame: TypeMoq.IMock<GlobalGame> = TypeMoq.Mock.ofType<
			GlobalGame
		>();

		mockedGlobalGame
			.setup(x => x.match_start)
			.returns(() => seasonDate.toDateString());
		mockedGameReplay
			.setup(x => x.global_game)
			.returns(() => mockedGlobalGame.object);

		return mockedGameReplay.object;
	};

	const currentSeasonGame: GameReplay = createReplay(new Date());
	const previousSeasonGame: GameReplay = createReplay(
		subMonths(new Date(), 1),
	);

	test("correctly matches the current season game result", () => {
		expect(seasonMatch(currentSeasonGame, "current")).toBe(true);
	});

	test("does not match the current season game result", () => {
		expect(seasonMatch(currentSeasonGame, "previous")).toBe(false);
	});

	test("correctly matches the previous season game result", () => {
		expect(seasonMatch(previousSeasonGame, "current")).toBe(false);
	});

	test("does not match the previous season game result", () => {
		expect(seasonMatch(previousSeasonGame, "previous")).toBe(true);
	});
});

describe("heroMatch", () => {
	const mockedPlayer: TypeMoq.IMock<GlobalGamePlayer> = TypeMoq.Mock.ofType<
		GlobalGamePlayer
	>();
	mockedPlayer.setup(x => x.hero_class_name).returns(() => "WARRIOR");

	test("returns true iff player matches hero", () => {
		const mockedCardData: TypeMoq.IMock<CardData> = TypeMoq.Mock.ofType<
			CardData
		>();
		const mockedGarrosh: TypeMoq.IMock<
			HearthstoneJSONCardData
		> = TypeMoq.Mock.ofType<HearthstoneJSONCardData>();

		mockedGarrosh.setup(x => x.cardClass).returns(() => "WARRIOR");
		mockedCardData
			.setup(x => x.fromCardId(TypeMoq.It.isValue<string>("HERO_01")))
			.returns(() => mockedGarrosh.object);

		expect(
			heroMatch(mockedCardData.object, mockedPlayer.object, "warrior"),
		).toBe(true);
		expect(
			heroMatch(mockedCardData.object, mockedPlayer.object, "warlock"),
		).toBe(false);
	});

	test("returns false on missing card data", () => {
		const mockedCardData: TypeMoq.IMock<CardData> = TypeMoq.Mock.ofType<
			CardData
		>();
		mockedCardData
			.setup(x => x.fromCardId(TypeMoq.It.isValue<string>("HERO_01")))
			.returns(() => null);

		expect(
			heroMatch(mockedCardData.object, mockedPlayer.object, "warrior"),
		).toBe(false);
	});
});
