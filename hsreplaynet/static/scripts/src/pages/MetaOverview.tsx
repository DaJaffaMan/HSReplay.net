
import PremiumWrapper from "../components/PremiumWrapper";
import InfoboxFilter from "../components/InfoboxFilter";
import UserData from "../UserData";
import InfoboxFilterGroup from "../components/InfoboxFilterGroup";
import * as React from "react";
import CardData from "../CardData";
import DataInjector from "../components/DataInjector";
import ArchetypeHeadToHead from "../components/metaoverview/ArchetypeHeadToHead";
import { SortDirection } from "../interfaces";
import TabList from "../components/layout/TabList";
import Tab from "../components/layout/Tab";
import ArchetypePopularity from "../components/metaoverview/popularity/ArchetypePopularity";

interface MetaOverviewState {
	mobileView?: boolean;
}

interface MetaOverviewProps {
	cardData: CardData;
	popularitySortBy?: string;
	setPopularitySortBy?: (popularitySortBy: string) => void;
	popularitySortDirection?: SortDirection;
	setPopularitySortDirection?: (ascending: SortDirection) => void;
	sortDirection?: SortDirection;
	setSortDirection?: (ascending: SortDirection) => void;
	sortBy?: string;
	setSortBy?: (sortBy: string) => void;
	gameType?: string;
	setGameType?: (gameType: string) => void;
	rankRange?: string;
	setRankRange?: (rankRange: string) => void;
	timeFrame?: string;
	setTimeFrame?: (timeFrame: string) => void;
	tab?: string;
	setTab?: (tab: string) => void;
}

const mobileWidth = 530;

export default class MetaOverview extends React.Component<MetaOverviewProps, MetaOverviewState> {

	constructor(props: MetaOverviewProps, context: any) {
		super(props, context);
		this.state = {
			mobileView: window.innerWidth <= mobileWidth,
		};
	}

	render(): JSX.Element {
		const params = {
			GameType: this.props.gameType,
			RankRange: this.props.rankRange,
			TimeRange: this.props.timeFrame,
		};

		const popularityParams = {
			GameType: this.props.gameType,
			TimeRange: this.props.timeFrame,
		};

		let content = null;

		const headToHead = (
			<DataInjector
				query={[
					{key: "archetypeData", params: {}, url: "/api/v1/archetypes/"},
					{key: "matchupData", params, url: "head_to_head_archetype_matchups"},
					{key: "popularityData", params, url: "archetype_popularity_distribution_stats"},
				]}
			>
				<ArchetypeHeadToHead
					cardData={this.props.cardData}
					mobileView={this.state.mobileView}
					setSortBy={this.props.setSortBy}
					setSortDirection={this.props.setSortDirection}
					sortBy={this.props.sortBy}
					sortDirection={this.props.sortDirection}
				/>
			</DataInjector>
		);

		if (this.state.mobileView) {
			content = headToHead;
		}
		else {
			content = (
				<TabList tab={this.props.tab} setTab={(tab) => this.props.setTab(tab)}>
					<Tab id="matchups" label="Matchups">
						{headToHead}
					</Tab>
					<Tab id="popularity" label="Popularity">
						<DataInjector
							query={[
								{key: "archetypeData", params: {}, url: "/api/v1/archetypes/"},
								{key: "popularityData", params: popularityParams, url: "archetype_popularity_by_rank"},
							]}
						>
							<ArchetypePopularity
								cardData={this.props.cardData}
								sortDirection={this.props.sortDirection}
								setSortDirection={this.props.setSortDirection}
								sortBy={this.props.popularitySortBy}
								setSortBy={this.props.setPopularitySortBy}
							/>
						</DataInjector>
					</Tab>
				</TabList>
			);
		}

		return <div className="meta-overview-container">
			<aside className="infobox">
				<h1>Meta Overview</h1>
				<section id="game-mode-filter">
					<h2>Game Mode</h2>
					<InfoboxFilterGroup
						selectedValue={this.props.gameType}
						onClick={(value) => this.props.setGameType(value)}
					>
						<InfoboxFilter value="RANKED_STANDARD">Ranked Standard</InfoboxFilter>
						<InfoboxFilter value="RANKED_WILD">Ranked Wild</InfoboxFilter>
					</InfoboxFilterGroup>
				</section>
				<section id="time-frame-filter">
					<PremiumWrapper>
						<h2>Time Frame</h2>
						<InfoboxFilterGroup
							selectedValue={this.props.timeFrame}
							onClick={(value) => this.props.setTimeFrame(value)}
						>
							<InfoboxFilter value="LAST_3_DAYS">Last 3 days</InfoboxFilter>
							<InfoboxFilter value="LAST_7_DAYS">Last 7 days</InfoboxFilter>
							<InfoboxFilter value="LAST_14_DAYS">Last 14 days</InfoboxFilter>
						</InfoboxFilterGroup>
					</PremiumWrapper>
				</section>
				<section id="rank-range-filter">
					<PremiumWrapper>
						<h2>Rank range</h2>
						<InfoboxFilterGroup
							locked={!UserData.isPremium()}
							selectedValue={this.props.rankRange}
							onClick={(value) => this.props.setRankRange(value)}
							tabIndex={0}
							disabled={this.props.tab === "popularity"}
						>
							<InfoboxFilter value="LEGEND_ONLY">Legend only</InfoboxFilter>
							<InfoboxFilter value="LEGEND_THROUGH_FIVE">Legend–5</InfoboxFilter>
							<InfoboxFilter value="LEGEND_THROUGH_TEN">Legend–10</InfoboxFilter>
							<InfoboxFilter value="ALL">Legend–25</InfoboxFilter>
						</InfoboxFilterGroup>
					</PremiumWrapper>
				</section>
			</aside>
			<main>
				{content}
			</main>
		</div>;
	}

	componentWillMount() {
		window.addEventListener("resize", this.onResize);
	}

	componentWillUnmount() {
		window.removeEventListener("resize", this.onResize);
	}

	onResize = () => {
		const width = window.innerWidth;
		if (this.state.mobileView && width > mobileWidth) {
			this.setState({mobileView: false});
		}
		else if (!this.state.mobileView && width <= mobileWidth) {
			this.setState({mobileView: true});
		}
	}
}
