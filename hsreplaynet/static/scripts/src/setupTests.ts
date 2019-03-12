import Enzyme from "enzyme";
import Adapter from "enzyme-adapter-react-16";
import "jest-enzyme";
import fetch from "jest-fetch-mock";

Enzyme.configure({ adapter: new Adapter() });

declare global {
	namespace NodeJS {
		interface Global {
			fetch: any;
		}
	}
}

global.fetch = fetch;

jest.mock("./UserData");
